from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import geopandas as gpd

import satn.compilation_dependencies as dependencies
import satn.compiler as compiler_module
from satn import compile
from satn.compilation_dependencies import compilation_dependency_manifest
from satn.models import CouncilConfig, TrafficLight
from satn.network_selection import NetworkSelectionProfile
from satn.sources import snapshot

PROJECT = Path(__file__).parents[1]


def prepared_config(tmp_path: Path) -> CouncilConfig:
    fixture = tmp_path / "fixture"
    shutil.copytree(
        PROJECT / "examples" / "fixture",
        fixture,
        ignore=shutil.ignore_patterns("work", ".satn-cache"),
    )
    config = CouncilConfig.from_yaml(fixture / "council.yaml")
    snapshot(config)
    return config


def copied_compiler_tree(tmp_path: Path) -> Path:
    """Exercise provenance through an installed-wheel-shaped package copy."""
    root = tmp_path / "compiler-tree"
    shutil.copytree(PROJECT / "src" / "satn", root / "satn")
    return root / "satn"


def test_backbone_recompilation_is_deterministic_without_legacy_pairwise_cache(
    tmp_path: Path,
) -> None:
    config = prepared_config(tmp_path)

    first = compile(config)
    second = compile(config)

    assert "cache" not in first.metadata
    assert "cache" not in second.metadata
    assert "publication_reused" not in first.metadata
    assert second.metadata["publication_reused"] is True
    assert first.run_id == second.run_id
    assert "connections" not in set(gpd.list_layers(second.artifacts["geopackage"])["name"])


def test_compiler_run_records_current_dependency_manifest_and_reuses_it(tmp_path: Path) -> None:
    config = prepared_config(tmp_path)

    first = compile(config)
    run = json.loads(first.artifacts["run"].read_text())
    second = compile(config)

    assert run["compilation_dependency_manifest"] == compilation_dependency_manifest()
    assert second.metadata["publication_reused"] is True


def test_compiler_dependency_change_invalidates_fixture_publication_reuse(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = prepared_config(tmp_path)
    first = compile(config)
    root = copied_compiler_tree(tmp_path)
    compiler_path = root / "compiler.py"
    compiler_path.write_bytes(
        compiler_path.read_bytes() + b"\n# dependency-manifest regression probe\n"
    )
    monkeypatch.setattr(dependencies, "_package_root", lambda: root)

    changed = compile(config)

    assert "publication_reused" not in changed.metadata
    assert changed.run_id != first.run_id


def test_review_map_change_does_not_invalidate_fixture_publication_reuse(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = prepared_config(tmp_path)
    compile(config)
    root = copied_compiler_tree(tmp_path)
    review_map = root / "assets" / "review-map.js"
    review_map.write_bytes(
        review_map.read_bytes() + b"\n/* dependency-manifest regression probe */\n"
    )
    monkeypatch.setattr(dependencies, "_package_root", lambda: root)

    unchanged = compile(config)

    assert unchanged.metadata["publication_reused"] is True


def test_full_directive_ignores_reusable_connections(tmp_path: Path) -> None:
    config = prepared_config(tmp_path)
    compile(config)
    config.compilation.full = True

    forced = compile(config)

    assert "cache" not in forced.metadata
    assert "publication_reused" not in forced.metadata
    assert forced.metadata["network_model"] == "backbone-outward"


def test_criteria_change_invalidates_all_reuse(tmp_path: Path) -> None:
    config = prepared_config(tmp_path)
    original = compile(config)
    config.compilation.criteria_version = "2-new-criterion"

    changed = compile(config)

    assert "cache" not in changed.metadata
    assert changed.run_id != original.run_id


def test_spine_access_preparation_fingerprint_directly_changes_final_run_id(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = prepared_config(tmp_path)
    config.compilation.network_selection = NetworkSelectionProfile.model_validate(
        {
            "profile_id": "run-fingerprint-fixture-v1",
            "candidate_source_precedence": [
                "verified-existing-asset",
                "a-road-corridor",
                "other-routable",
            ],
        }
    )
    first = compile(config)
    original_prepare = compiler_module.prepare_spine_access_candidates

    def changed_prepare(*args, **kwargs):
        prepared = original_prepare(*args, **kwargs)
        return replace(prepared, preparation_fingerprint="f" * 64)

    monkeypatch.setattr(compiler_module, "prepare_spine_access_candidates", changed_prepare)
    config.compilation.full = True
    changed = compile(config)

    assert first.metadata["spine_access_candidate_preparation"][
        "preparation_fingerprint"
    ] != changed.metadata["spine_access_candidate_preparation"][
        "preparation_fingerprint"
    ]
    assert changed.run_id != first.run_id


def test_agent_review_policy_change_invalidates_publication_reuse(tmp_path: Path) -> None:
    config = prepared_config(tmp_path)
    original = compile(config)
    config.compilation.agent.review_statuses = (TrafficLight.GREEN,)

    changed = compile(config)

    assert "publication_reused" not in changed.metadata
    assert changed.run_id != original.run_id
    assert changed.status == "decision-required"
    assert changed.artifacts == {}
    assert changed.decision_requests[0].status == TrafficLight.GREEN


def test_invalid_divergence_audit_prevents_publication_reuse(tmp_path: Path) -> None:
    config = prepared_config(tmp_path)
    first = compile(config)
    divergences_path = first.artifacts["divergences"]
    divergences = json.loads(divergences_path.read_text())
    divergences["records"] = [{"connection_id": "invalid-divergence"}]
    divergences_path.write_text(json.dumps(divergences))

    recompiled = compile(config)

    assert "publication_reused" not in recompiled.metadata
    restored = json.loads(recompiled.artifacts["divergences"].read_text())
    assert restored["records"] == []


def test_stale_agent_review_summary_prevents_publication_reuse(tmp_path: Path) -> None:
    config = prepared_config(tmp_path)
    first = compile(config)
    run = json.loads(first.artifacts["run"].read_text())
    run["agent_review"]["reviewed_decisions"] += 1
    first.artifacts["run"].write_text(json.dumps(run))

    recompiled = compile(config)

    assert "publication_reused" not in recompiled.metadata
    restored = json.loads(recompiled.artifacts["run"].read_text())
    assert restored["agent_review"]["reviewed_decisions"] == 0


def test_changed_elevation_evidence_changes_run_fingerprint(
    tmp_path: Path,
) -> None:
    config = prepared_config(tmp_path)
    first = compile(config)
    terrain_path = config.source.national_elevation.path
    assert terrain_path is not None
    terrain = gpd.read_file(terrain_path)
    terrain.loc[0, "elevation_m"] = float(terrain.loc[0, "elevation_m"]) + 1
    terrain.to_file(terrain_path, driver="GeoJSON")
    snapshot(config, replace=True)

    changed = compile(config)

    assert "cache" not in changed.metadata
    assert changed.run_id != first.run_id


def test_cli_full_directive_forces_recompilation(tmp_path: Path) -> None:
    config = prepared_config(tmp_path)
    compile(config)

    completed = subprocess.run(
        [
            str(PROJECT / ".venv" / "bin" / "satn"),
            "compile",
            str(config.config_path),
            "--full",
            "--log-level",
            "DEBUG",
        ],
        cwd=PROJECT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "INFO satn.pipeline: Compilation started" in completed.stderr
    assert "INFO satn.backbone: Backbone assembly started" in completed.stderr
    assert "INFO satn.publisher: Publication atomically replaced" in completed.stderr

    run = json.loads((config.publication.output_dir / "run.json").read_text())
    assert "cache" not in run
    assert run["network_model"] == "backbone-outward"
    diagnostics = run["compilation_diagnostics"]
    assert diagnostics["assembly_strategy"] == "backbone-outward"
    assert diagnostics["candidate_evaluations"] > 0
    assert diagnostics["road_graph_edges"] >= diagnostics["reciprocal_routing_edges"]
    cross_spine = diagnostics["cross_spine"]
    assert cross_spine["schema_version"] == "cross-spine-diagnostics/v2"
    assert cross_spine["candidate_connectors"] >= cross_spine["authoritative_connectors"]
    assert not any("second" in key or "throughput" in key for key in cross_spine)
