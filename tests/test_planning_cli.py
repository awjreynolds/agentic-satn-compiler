from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

import satn.cli as cli
import satn.planning_cli as planning_cli


def _config(path: Path) -> Path:
    path.write_text(
        """\
council_id: tiny
council_name: Tiny Council
source:
  snapshot_dir: snapshots
publication:
  output_dir: output
  title: Tiny publication
""",
        encoding="utf-8",
    )
    return path


def test_plan_run_uses_explicit_roots_and_branch(tmp_path: Path, monkeypatch) -> None:
    observed: dict[str, object] = {}

    class StubRuntime:
        def __init__(self, root: Path, **kwargs: object) -> None:
            observed["root"] = root
            observed["constructor"] = kwargs

        def run(self, config: object, **kwargs: object) -> SimpleNamespace:
            observed["config"] = config
            observed["run"] = kwargs
            return SimpleNamespace(
                as_dict=lambda: {
                    "status": "reviewable-incomplete",
                    "branch_id": kwargs["branch"],
                    "mode": kwargs["mode"],
                }
            )

    monkeypatch.setattr(planning_cli, "PlanningRuntime", StubRuntime)
    response = CliRunner().invoke(
        cli.app,
        [
            "plan",
            "run",
            str(_config(tmp_path / "area.yaml")),
            "--root",
            str(tmp_path / "history"),
            "--output-root",
            str(tmp_path / "output"),
            "--branch",
            "case-bath-keynsham",
            "--mode",
            "deterministic",
        ],
    )

    assert response.exit_code == 0, response.output
    payload = json.loads(response.stdout)
    assert payload["branch_id"] == "case-bath-keynsham"
    assert observed["root"] == tmp_path / "history"
    assert observed["run"] == {
        "output_root": tmp_path / "output",
        "branch": "case-bath-keynsham",
        "mode": "deterministic",
    }


def test_plan_verify_and_compare_are_read_only_branch_operations(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    class StubRuntime:
        def __init__(self, root: Path, **_kwargs: object) -> None:
            calls.append(("init", (root,), {}))

        def verify(self, branch: str) -> dict[str, object]:
            calls.append(("verify", (branch,), {}))
            return {"valid": True, "branch_id": branch}

        def compare(self, base: str, branch: str) -> dict[str, object]:
            calls.append(("compare", (base, branch), {}))
            return {"base_branch_id": base, "branch_id": branch}

    monkeypatch.setattr(planning_cli, "PlanningRuntime", StubRuntime)
    runner = CliRunner()
    verify = runner.invoke(
        cli.app,
        ["plan", "verify", str(tmp_path / "history"), "--branch", "main"],
    )
    compare = runner.invoke(
        cli.app,
        [
            "plan",
            "compare",
            str(tmp_path / "history"),
            "--base-branch",
            "main",
            "--branch",
            "alternative",
        ],
    )

    assert verify.exit_code == 0, verify.output
    assert json.loads(verify.stdout) == {"branch_id": "main", "valid": True}
    assert compare.exit_code == 0, compare.output
    assert json.loads(compare.stdout) == {
        "base_branch_id": "main",
        "branch_id": "alternative",
    }
    assert ("verify", ("main",), {}) in calls
    assert ("compare", ("main", "alternative"), {}) in calls
