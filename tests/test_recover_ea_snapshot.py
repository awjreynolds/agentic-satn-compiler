from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from satn.compilation_dependencies import compilation_dependency_manifest
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


def test_recovery_transaction_accepts_only_recovery_path_fingerprint() -> None:
    module = _recovery_script()
    config = AreaDefinition.from_yaml(PROJECT / "deployments" / "banes" / "area.yaml")
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
