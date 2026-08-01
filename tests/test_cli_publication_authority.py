from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

import satn.cli as cli


def test_compile_cli_passes_an_explicit_external_publication_capability(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    config = tmp_path / "area.yaml"
    config.write_text(
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
    workspace = tmp_path / "workspace"
    approved = tmp_path / "external-output"
    observed: dict[str, object] = {}

    def compile_stub(*_args: object, **kwargs: object) -> SimpleNamespace:
        observed.update(kwargs)
        return SimpleNamespace(status="complete", connections=1, gaps=0, output_dir=approved)

    monkeypatch.setattr(cli, "compile_satn", compile_stub)
    response = CliRunner().invoke(
        cli.app,
        [
            "compile",
            str(config),
            "--publication-workspace-root",
            str(workspace),
            "--approved-external-publication-destination",
            str(approved),
            "--expected-prior-run-fingerprint",
            "a" * 64,
        ],
    )

    assert response.exit_code == 0, response.output
    authority = observed["publication_authority"]
    assert authority.workspace_root == workspace
    assert authority.approved_external_destination == approved
    assert authority.expected_prior_run_fingerprint == "a" * 64
