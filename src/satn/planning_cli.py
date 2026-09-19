"""Experimental ``satn plan`` commands."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated

import typer

from satn.models import AreaDefinition
from satn.planning_runtime import PlanningRuntime

plan_app = typer.Typer(
    no_args_is_help=True,
    help="Run and inspect the experimental durable planning history.",
)


def _emit(value: object) -> None:
    if hasattr(value, "as_dict"):
        value = value.as_dict()  # type: ignore[union-attr]
    typer.echo(json.dumps(value, sort_keys=True, indent=2, default=str))


@plan_app.command("run")
def run_command(
    config: Path,
    root: Annotated[
        Path,
        typer.Option("--root", help="Explicit local planning history/artifact root."),
    ],
    output_root: Annotated[
        Path,
        typer.Option("--output-root", help="Separate review output directory."),
    ],
    branch: str = typer.Option("main", "--branch"),
    mode: str = typer.Option("deterministic", "--mode"),
    connection_options: Annotated[
        Path | None,
        typer.Option(
            "--connection-options",
            help="JSON list of admitted named-place connection choices for this run.",
        ),
    ] = None,
) -> None:
    """Admit one area and write a reviewable planning result."""

    area = AreaDefinition.from_yaml(config)
    options: list[Mapping[str, object]] = []
    if connection_options is not None:
        payload = json.loads(connection_options.read_text(encoding="utf-8"))
        if not isinstance(payload, list) or any(not isinstance(item, Mapping) for item in payload):
            raise typer.BadParameter("connection options must be a JSON list of objects")
        options = [dict(item) for item in payload]
    kwargs: dict[str, object] = {
        "output_root": output_root,
        "branch": branch,
        "mode": mode,
    }
    if options:
        kwargs["connection_options"] = options
    result = PlanningRuntime(root).run(area, **kwargs)  # type: ignore[arg-type]
    _emit(result)


@plan_app.command("verify")
def verify_command(
    root: Path,
    branch: str = typer.Option("main", "--branch"),
) -> None:
    """Verify one branch and its immutable dependency closure."""

    _emit(PlanningRuntime(root).verify(branch))


@plan_app.command("replay")
def replay_command(
    root: Path,
    branch: str = typer.Option("main", "--branch"),
) -> None:
    """Replay one branch without provider or source dispatch."""

    _emit(PlanningRuntime(root).replay(branch))


@plan_app.command("fork")
def fork_command(
    root: Path,
    checkpoint: str,
    branch: Annotated[
        str,
        typer.Option("--branch", help="Stable child branch name."),
    ],
) -> None:
    """Create a child branch at a pre-decision checkpoint."""

    _emit(PlanningRuntime(root).fork(checkpoint, branch).as_dict())


@plan_app.command("advance")
def advance_command(
    root: Path,
    operation: Path,
    branch: Annotated[str, typer.Option("--branch")] = "main",
    expected_head: Annotated[str | None, typer.Option("--expected-head")] = None,
    output_root: Annotated[Path | None, typer.Option("--output-root")] = None,
) -> None:
    """Apply one explicit operation after offline prefix replay."""

    payload = json.loads(operation.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise typer.BadParameter("operation file must contain one JSON object")
    _emit(
        PlanningRuntime(root).advance(
            branch,
            payload,
            expected_head=expected_head,
            output_root=output_root,
        )
    )


@plan_app.command("compare")
def compare_command(
    root: Path,
    base_branch: Annotated[
        str,
        typer.Option("--base-branch", help="Pinned branch used as the comparison base."),
    ],
    branch: str = typer.Option("main", "--branch"),
) -> None:
    """Compare branch decisions and dependency invalidation."""

    _emit(PlanningRuntime(root).compare(base_branch, branch))


__all__ = ["plan_app"]
