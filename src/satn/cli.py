"""Command-line interface."""

import logging
from pathlib import Path
from typing import Annotated

import typer

from satn.deployment_scenario_cli import scenario_app
from satn.ea_fixed_point_operations import run_ea_fixed_point_convergence
from satn.evidence_cli import evidence_app
from satn.filesystem_safety import (
    default_publication_destination_authority,
    publication_destination_authority,
)
from satn.models import AreaDefinition
from satn.parallel_reduction_corpus_cli import corpus_app
from satn.pipeline import compile as compile_satn
from satn.pipeline import compile_ea_recovery_candidate
from satn.sources import snapshot as create_snapshot

app = typer.Typer(no_args_is_help=True, help="Compile strategic active travel networks.")
app.add_typer(evidence_app, name="evidence")
app.add_typer(scenario_app, name="scenario")
app.add_typer(corpus_app, name="corpus")
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
    retain_core: bool = typer.Option(False, "--retain-core"),
    log_level: str = typer.Option("INFO", "--log-level"),
) -> None:
    """Create, validate, or atomically augment an immutable source snapshot."""
    _configure_logging(log_level)
    try:
        path = create_snapshot(
            AreaDefinition.from_yaml(config), replace=replace, retain_core=retain_core
        )
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
    publication_workspace_root: Annotated[
        Path | None,
        typer.Option(
            "--publication-workspace-root",
            help="Caller-owned root beneath which publication is permitted.",
        ),
    ] = None,
    approved_external_publication_destination: Annotated[
        Path | None,
        typer.Option(
            "--approved-external-publication-destination",
            help=(
                "Explicitly permit this exact publication destination outside the "
                "workspace root."
            ),
        ),
    ] = None,
    expected_prior_run_fingerprint: Annotated[
        str | None,
        typer.Option(
            "--expected-prior-run-fingerprint",
            help=(
                "Authorize replacement of a pre-marker output with this exact prior "
                "fingerprint."
            ),
        ),
    ] = None,
    log_level: str = typer.Option("INFO", "--log-level"),
) -> None:
    """Compile and atomically publish the current network."""
    _configure_logging(log_level)
    council = AreaDefinition.from_yaml(config)
    council.compilation.full = full
    authority = None
    if (
        publication_workspace_root is not None
        or approved_external_publication_destination is not None
        or expected_prior_run_fingerprint is not None
    ):
        default_authority = default_publication_destination_authority(config)
        authority = publication_destination_authority(
            workspace_root=publication_workspace_root or default_authority.workspace_root,
            approved_external_destination=approved_external_publication_destination,
            expected_prior_run_fingerprint=expected_prior_run_fingerprint,
        )
    try:
        result = compile_satn(
            council,
            decision_ledger=decision_ledger,
            publication_authority=authority,
        )
    except Exception:
        LOGGER.exception("Compile command failed config=%s", config)
        raise
    if result.status in {"decision-required", "terminated"}:
        typer.echo(result.model_dump_json(indent=2))
        return
    typer.echo(f"{result.status}: {result.connections} connections, {result.gaps} gaps")
    typer.echo(result.output_dir)


@app.command("compile-ea-recovery-candidate")
def ea_recovery_candidate_command(
    config: Path,
    log_level: str = typer.Option("INFO", "--log-level"),
) -> None:
    """Retain a governed replacement candidate for the pinned invalid WECA v10."""

    _configure_logging(log_level)
    try:
        candidate = compile_ea_recovery_candidate(config)
    except Exception:
        LOGGER.exception("EA recovery candidate command failed config=%s", config)
        raise
    typer.echo(candidate)


@app.command("converge-ea-elevation")
def fixed_point_convergence_command(
    config: Path,
    max_iterations: int = typer.Option(
        4,
        "--max-iterations",
        min=1,
        max=10,
        help="Maximum full compile comparisons before refusing publication.",
    ),
    record: Annotated[
        Path | None,
        typer.Option(
            "--record",
            help="Write the immutable expected/actual convergence history here.",
        ),
    ] = None,
    resume: bool = typer.Option(
        False,
        "--resume",
        help="Resume the exact in-progress run held in --record.",
    ),
    log_level: str = typer.Option("INFO", "--log-level"),
) -> None:
    """Compile, reacquire and snapshot until governed EA routes reach equality."""
    _configure_logging(log_level)
    try:
        result = run_ea_fixed_point_convergence(
            config,
            max_iterations=max_iterations,
            record_path=record,
            resume=resume,
        )
    except Exception:
        LOGGER.exception("EA fixed-point convergence failed config=%s", config)
        raise
    typer.echo(f"{result.status}: {len(result.iterations)} compile comparison(s)")
    typer.echo(result.record_path)
    if result.status != "converged":
        raise typer.Exit(code=2)


if __name__ == "__main__":
    app()
