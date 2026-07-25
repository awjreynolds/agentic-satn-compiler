"""Command-line interface."""

import logging
from pathlib import Path
from typing import Annotated

import typer

from satn.models import AreaDefinition
from satn.pipeline import compile as compile_satn
from satn.sources import snapshot as create_snapshot

app = typer.Typer(no_args_is_help=True, help="Compile strategic active travel networks.")
LOGGER = logging.getLogger(__name__)


def _configure_logging(log_level: str) -> None:
    level = getattr(logging, log_level.upper(), None)
    if not isinstance(level, int):
        raise typer.BadParameter(
            "expected DEBUG, INFO, WARNING, ERROR or CRITICAL",
            param_hint="--log-level",
        )
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


@app.command()
def snapshot(
    config: Path,
    replace: bool = typer.Option(False, "--replace"),
    log_level: str = typer.Option("INFO", "--log-level"),
) -> None:
    """Create or validate an immutable source snapshot."""
    _configure_logging(log_level)
    try:
        path = create_snapshot(AreaDefinition.from_yaml(config), replace=replace)
    except Exception:
        LOGGER.exception("Snapshot command failed config=%s", config)
        raise
    typer.echo(path)


@app.command("compile")
def compile_command(
    config: Path,
    decision_ledger: Annotated[
        Path | None,
        typer.Option(
            "--decision-ledger",
            help=(
                "JSON ledger containing only request, fingerprint and offered-choice "
                "identifiers."
            ),
        ),
    ] = None,
    full: bool = typer.Option(
        False,
        "--full",
        help="Force recompilation instead of reusing an input-identical validated publication.",
    ),
    log_level: str = typer.Option("INFO", "--log-level"),
) -> None:
    """Compile and atomically publish the current network."""
    _configure_logging(log_level)
    council = AreaDefinition.from_yaml(config)
    council.compilation.full = full
    try:
        result = compile_satn(council, decision_ledger=decision_ledger)
    except Exception:
        LOGGER.exception("Compile command failed config=%s", config)
        raise
    if result.status in {"decision-required", "terminated"}:
        typer.echo(result.model_dump_json(indent=2))
        return
    typer.echo(f"{result.status}: {result.connections} connections, {result.gaps} gaps")
    typer.echo(result.output_dir)


if __name__ == "__main__":
    app()
