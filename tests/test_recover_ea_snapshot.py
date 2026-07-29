from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from satn.compilation_dependencies import compilation_dependency_manifest
from satn.ea_snapshot_recovery import EARecoveryClosureProof
from satn.models import AreaDefinition
from satn.pipeline import compilation_governed_input_fingerprint

PROJECT = Path(__file__).parents[1]
SCRIPT = PROJECT / "scripts" / "recover_ea_snapshot.py"


def _recovery_script() -> ModuleType:
    specification = importlib.util.spec_from_file_location(
        "recover_ea_snapshot",
        SCRIPT,
    )
    if specification is None or specification.loader is None:
        raise AssertionError("recovery script cannot be imported")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_recovery_transaction_accepts_only_recovery_path_fingerprint(
    tmp_path: Path,
) -> None:
    module = _recovery_script()
    config = AreaDefinition.from_yaml(PROJECT / "deployments" / "banes" / "area.yaml")
    snapshot_id = "minimal-recovery-transaction-snapshot"
    snapshot_root = tmp_path / "snapshots"
    snapshot = snapshot_root / snapshot_id
    snapshot.mkdir(parents=True)
    (snapshot / "snapshot.json").write_text(
        '{"schema_version":"2.0","snapshot_id":"minimal-recovery-transaction-snapshot"}\n',
        encoding="utf-8",
    )
    config.source.snapshot_dir = snapshot_root
    config.source.snapshot_id = snapshot_id
    network_manifest = compilation_dependency_manifest(config)
    recovery_manifest = compilation_dependency_manifest(
        config,
        compiler_path="ea-recovery",
    )
    network_fingerprint = compilation_governed_input_fingerprint(
        config,
        dependency_manifest=network_manifest,
    )
    recovery_fingerprint = compilation_governed_input_fingerprint(
        config,
        dependency_manifest=recovery_manifest,
    )

    assert network_fingerprint != recovery_fingerprint
    assert module._validate_recovery_transaction_candidate_fingerprint(
        config,
        {"governed_input_fingerprint": recovery_fingerprint},
    ) == recovery_fingerprint
    with pytest.raises(ValueError, match="fingerprint is stale"):
        module._validate_recovery_transaction_candidate_fingerprint(
            config,
            {"governed_input_fingerprint": network_fingerprint},
        )


def _completed_bridge_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[ModuleType, dict[str, Path | bytes | dict[str, object]]]:
    module = _recovery_script()
    config_path = tmp_path / "area.yaml"
    prepared_path = tmp_path / ".recovery.prepared.yaml"
    convergence_path = tmp_path / ".recovery.convergence.json"
    record_path = tmp_path / "recovery.json"
    original = b"source:\n  snapshot_id: legacy-v10\n"
    prepared = b"source:\n  snapshot_id: recovered-v11\n"
    finalized = b"source:\n  snapshot_id: recovered-v12\n"
    config_path.write_bytes(original)
    prepared_path.write_bytes(prepared)
    convergence_path.write_text('{"status":"converged"}', encoding="utf-8")
    manifest_sha256 = "a" * 64
    proof = EARecoveryClosureProof.create(
        target_snapshot_id="recovered-v12",
        target_manifest_sha256=manifest_sha256,
        manifest_elevation_primary_fingerprint="b" * 64,
        candidate_network_sha256="c" * 64,
        governed_input_fingerprint="d" * 64,
        expected_eligible_route_fingerprint="b" * 64,
        actual_eligible_route_fingerprint="b" * 64,
    )
    closure_path = convergence_path.with_name(
        f"{convergence_path.stem}.closure.json"
    )

    def run(
        path: Path,
        *,
        max_iterations: int,
        record_path: Path,
        resume: bool,
    ) -> SimpleNamespace:
        assert path == prepared_path
        assert max_iterations == 3
        assert record_path == convergence_path
        assert resume is True
        prepared_path.write_bytes(finalized)
        closure_path.write_text(
            json.dumps(
                {
                    "schema_version": "ea-fixed-point-finalization/v1",
                    "convergence_record_sha256": hashlib.sha256(
                        convergence_path.read_bytes()
                    ).hexdigest(),
                    "original_configuration_sha256": hashlib.sha256(
                        prepared
                    ).hexdigest(),
                    "promoted_configuration_sha256": hashlib.sha256(
                        finalized
                    ).hexdigest(),
                    "fixed_point_closure": proof.record(),
                }
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(
            status="converged",
            final_snapshot=SimpleNamespace(
                snapshot_id=proof.target_snapshot_id,
                manifest_sha256=proof.target_manifest_sha256,
            ),
        )

    monkeypatch.setattr(module, "run_ea_fixed_point_convergence", run)
    return module, {
        "config_path": config_path,
        "prepared_path": prepared_path,
        "convergence_path": convergence_path,
        "record_path": record_path,
        "original": original,
        "finalized": finalized,
        "base_record": {
            "schema_version": "ea-snapshot-recovery/v1",
            "status": "candidate-reconciled",
            "target_snapshot_id": "recovered-v11",
        },
    }


def test_bounded_bridge_writes_governed_record_before_main_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, case = _completed_bridge_case(tmp_path, monkeypatch)
    replace = module.atomic_replace_recovery_configuration

    def replace_after_record(path: Path, content: bytes) -> None:
        assert Path(case["record_path"]).is_file()
        replace(path, content)

    monkeypatch.setattr(module, "atomic_replace_recovery_configuration", replace_after_record)
    result = module._complete_bounded_recovery(
        config_path=Path(case["config_path"]),
        prepared_config_path=Path(case["prepared_path"]),
        convergence_record_path=Path(case["convergence_path"]),
        recovery_record_path=Path(case["record_path"]),
        base_record=dict(case["base_record"]),
        max_iterations=3,
        expected_prepared_config_bytes=b"source:\n  snapshot_id: recovered-v11\n",
    )

    record = json.loads(Path(case["record_path"]).read_text(encoding="utf-8"))
    assert result.name == "recovered-v12"
    assert record["status"] == "sealed"
    assert record["target_snapshot_id"] == "recovered-v12"
    assert record["fixed_point_closure"]["actual_eligible_route_fingerprint"] == (
        "b" * 64
    )
    assert Path(case["config_path"]).read_bytes() == case["finalized"]


def test_bounded_bridge_refuses_non_convergence_without_committing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _recovery_script()
    config_path = tmp_path / "area.yaml"
    prepared_path = tmp_path / "prepared.yaml"
    convergence_path = tmp_path / "convergence.json"
    record_path = tmp_path / "recovery.json"
    original = b"original"
    config_path.write_bytes(original)
    prepared_path.write_bytes(b"prepared")
    monkeypatch.setattr(
        module,
        "run_ea_fixed_point_convergence",
        lambda *_args, **_kwargs: SimpleNamespace(status="non-converged"),
    )

    with pytest.raises(ValueError, match="did not converge"):
        module._complete_bounded_recovery(
            config_path=config_path,
            prepared_config_path=prepared_path,
            convergence_record_path=convergence_path,
            recovery_record_path=record_path,
            base_record={"schema_version": "ea-snapshot-recovery/v1"},
            max_iterations=3,
            expected_prepared_config_bytes=b"prepared",
        )

    assert config_path.read_bytes() == original
    assert not record_path.exists()


def test_bounded_bridge_refuses_substituted_prepared_configuration_before_convergence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _recovery_script()
    config_path = tmp_path / "area.yaml"
    prepared_path = tmp_path / "prepared.yaml"
    convergence_path = tmp_path / "convergence.json"
    record_path = tmp_path / "recovery.json"
    config_path.write_bytes(b"original")
    prepared_path.write_bytes(b"substituted")
    monkeypatch.setattr(
        module,
        "run_ea_fixed_point_convergence",
        lambda *_args, **_kwargs: pytest.fail("substituted input reached convergence"),
    )

    with pytest.raises(ValueError, match="prepared configuration identity differs"):
        module._complete_bounded_recovery(
            config_path=config_path,
            prepared_config_path=prepared_path,
            convergence_record_path=convergence_path,
            recovery_record_path=record_path,
            base_record={"schema_version": "ea-snapshot-recovery/v1"},
            max_iterations=3,
            expected_prepared_config_bytes=b"deterministic",
        )

    assert config_path.read_bytes() == b"original"
    assert not record_path.exists()


def test_bounded_bridge_accepts_terminal_hash_bound_prepared_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, case = _completed_bridge_case(tmp_path, monkeypatch)
    write_record = module.write_recovery_record
    arguments = {
        "config_path": Path(case["config_path"]),
        "prepared_config_path": Path(case["prepared_path"]),
        "convergence_record_path": Path(case["convergence_path"]),
        "recovery_record_path": Path(case["record_path"]),
        "base_record": dict(case["base_record"]),
        "max_iterations": 3,
        "expected_prepared_config_bytes": b"source:\n  snapshot_id: recovered-v11\n",
    }
    monkeypatch.setattr(
        module,
        "write_recovery_record",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("crash before governed record")
        ),
    )

    with pytest.raises(RuntimeError, match="crash before governed record"):
        module._complete_bounded_recovery(**arguments)
    assert not Path(case["record_path"]).exists()
    assert Path(case["prepared_path"]).read_bytes() == case["finalized"]

    monkeypatch.setattr(module, "write_recovery_record", write_record)
    module._complete_bounded_recovery(**arguments)

    assert Path(case["record_path"]).is_file()
    assert Path(case["config_path"]).read_bytes() == case["finalized"]


def test_completed_bridge_revalidates_terminal_artifacts_before_replacing_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, case = _completed_bridge_case(tmp_path, monkeypatch)
    arguments = {
        "config_path": Path(case["config_path"]),
        "prepared_config_path": Path(case["prepared_path"]),
        "convergence_record_path": Path(case["convergence_path"]),
        "recovery_record_path": Path(case["record_path"]),
        "base_record": dict(case["base_record"]),
        "max_iterations": 3,
        "expected_prepared_config_bytes": b"source:\n  snapshot_id: recovered-v11\n",
    }
    monkeypatch.setattr(
        module,
        "atomic_replace_recovery_configuration",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("crash after record")
        ),
    )
    with pytest.raises(RuntimeError, match="crash after record"):
        module._complete_bounded_recovery(**arguments)

    monkeypatch.setattr(
        module,
        "run_ea_fixed_point_convergence",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("EA terminal snapshot manifest SHA-256 differs")
        ),
    )
    monkeypatch.setattr(
        module,
        "atomic_replace_recovery_configuration",
        lambda *_args, **_kwargs: pytest.fail(
            "configuration replaced before terminal revalidation"
        ),
    )
    with pytest.raises(ValueError, match="snapshot manifest SHA-256 differs"):
        module._complete_bounded_recovery(**arguments)

    assert Path(case["config_path"]).read_bytes() == case["original"]


def test_bounded_bridge_resumes_record_before_config_without_recompiling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, case = _completed_bridge_case(tmp_path, monkeypatch)
    monkeypatch.setattr(
        module,
        "atomic_replace_recovery_configuration",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("crash after record")
        ),
    )
    arguments = {
        "config_path": Path(case["config_path"]),
        "prepared_config_path": Path(case["prepared_path"]),
        "convergence_record_path": Path(case["convergence_path"]),
        "recovery_record_path": Path(case["record_path"]),
        "base_record": dict(case["base_record"]),
        "max_iterations": 3,
        "expected_prepared_config_bytes": b"source:\n  snapshot_id: recovered-v11\n",
    }

    with pytest.raises(RuntimeError, match="crash after record"):
        module._complete_bounded_recovery(**arguments)
    assert Path(case["record_path"]).is_file()
    assert Path(case["config_path"]).read_bytes() == case["original"]

    terminal_resumes: list[Path] = []

    def resume_terminal(
        path: Path,
        *,
        max_iterations: int,
        record_path: Path,
        resume: bool,
    ) -> SimpleNamespace:
        assert max_iterations == 3
        assert record_path == case["convergence_path"]
        assert resume is True
        terminal_resumes.append(path)
        return SimpleNamespace(
            status="converged",
            final_snapshot=SimpleNamespace(
                snapshot_id="recovered-v12",
                manifest_sha256="a" * 64,
            ),
        )

    monkeypatch.setattr(module, "run_ea_fixed_point_convergence", resume_terminal)
    monkeypatch.setattr(
        module,
        "atomic_replace_recovery_configuration",
        lambda path, content: path.write_bytes(content),
    )
    module._complete_bounded_recovery(**arguments)

    assert terminal_resumes == [case["prepared_path"]]
    assert Path(case["config_path"]).read_bytes() == case["finalized"]
