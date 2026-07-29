"""Typer and rendering adapter for Local Evidence Store operations."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

import typer

from satn.local_evidence_store import EvidenceWriterBusy, LocalEvidenceStore

OutputFormat = Literal["text", "json"]


@dataclass(frozen=True)
class EvidenceCliContext:
    """The deep workspace interface plus the selected renderer."""

    operations: Any
    output_format: OutputFormat


evidence_app = typer.Typer(
    no_args_is_help=True,
    help="Manage one offline workspace-local Local Evidence Store.",
)


@evidence_app.callback()
def evidence_options(
    ctx: typer.Context,
    workspace: Annotated[
        Path | None,
        typer.Option("--workspace", help="Workspace used by the default store path."),
    ] = None,
    store: Annotated[
        Path | None,
        typer.Option("--store", help="Explicit Local Evidence Store path."),
    ] = None,
    extension_cache: Annotated[
        Path | None,
        typer.Option("--extension-cache", help="Explicit pinned Spatial extension cache."),
    ] = None,
    output_format: Annotated[
        OutputFormat,
        typer.Option("--format", help="Output format: text or json."),
    ] = "text",
) -> None:
    """Bind raw CLI paths to the store-owned workspace interface."""

    ctx.obj = EvidenceCliContext(
        operations=LocalEvidenceStore.workspace(
            workspace=workspace,
            store=store,
            extension_cache=extension_cache,
            invocation_dir=Path.cwd(),
        ),
        output_format=output_format,
    )


@evidence_app.command("init")
def initialise_command(
    ctx: typer.Context,
    extension_archive: Annotated[
        Path | None,
        typer.Option(
            "--extension-archive",
            help="Caller-supplied pinned Spatial binary; never a URL.",
        ),
    ] = None,
) -> None:
    """Initialise or verify the exact offline store runtime."""

    _execute(
        _context(ctx),
        "init",
        lambda: _context(ctx).operations.initialise(
            extension_archive=extension_archive,
        ),
    )


@evidence_app.command("refresh")
def refresh_command(
    ctx: typer.Context,
    area: Path,
    source_export: Annotated[
        list[Path] | None,
        typer.Option(
            "--source-export",
            help="Local governed Source Export descriptor; repeat for multiple layers.",
        ),
    ] = None,
    replace_source: Annotated[
        str | None,
        typer.Option("--replace-source", help="Source layer explicitly allowed to change."),
    ] = None,
    expect_state: Annotated[
        str | None,
        typer.Option("--expect-state", help="Exact current state required for replacement."),
    ] = None,
    dry_run: bool = typer.Option(False, "--dry-run"),
    rebuild: bool = typer.Option(False, "--rebuild"),
) -> None:
    """Plan, refresh, or rebuild exact disconnected Evidence Coverage."""

    context = _context(ctx)
    _execute(
        context,
        "refresh",
        lambda: context.operations.refresh(
            area=area,
            source_exports=tuple(source_export or ()),
            replace_source=replace_source,
            expect_state=expect_state,
            dry_run=dry_run,
            rebuild=rebuild,
        ),
    )


@evidence_app.command("status")
def status_command(
    ctx: typer.Context,
    area: Annotated[
        Path | None,
        typer.Option("--area", help="Compare coverage with this Area Definition."),
    ] = None,
    state: Annotated[
        str | None,
        typer.Option("--state", help="Inspect this exact historical coverage state."),
    ] = None,
    verify: bool = typer.Option(False, "--verify"),
    provenance: bool = typer.Option(False, "--provenance"),
) -> None:
    """Report current or historical Evidence Coverage and diagnostics."""

    context = _context(ctx)
    _execute(
        context,
        "status",
        lambda: context.operations.status(
            area=area,
            state=state,
            verify=verify,
            provenance=provenance,
        ),
    )


@evidence_app.command("query")
def query_command(
    ctx: typer.Context,
    layer: str,
    area: Annotated[
        Path | None,
        typer.Option("--area", help="Use this Area Definition boundary."),
    ] = None,
    bbox: Annotated[
        str | None,
        typer.Option("--bbox", help="BNG xmin,ymin,xmax,ymax."),
    ] = None,
    geometry: Annotated[
        str | None,
        typer.Option("--geometry", help="Inline GeoJSON geometry or local GeoJSON path."),
    ] = None,
    predicate: Literal["intersects", "within", "contains"] = typer.Option(
        "intersects",
        "--predicate",
    ),
    where: Annotated[
        list[str] | None,
        typer.Option("--where", help="Declared equality filter FIELD=JSON."),
    ] = None,
    field: Annotated[
        list[str] | None,
        typer.Option("--field", help="Declared attribute to include."),
    ] = None,
    state: Annotated[
        str | None,
        typer.Option("--state", help="Exact historical coverage state."),
    ] = None,
    export_gpkg: Annotated[
        Path | None,
        typer.Option("--export-gpkg", help="Generated inspection export."),
    ] = None,
    replace_export: bool = typer.Option(False, "--replace-export"),
) -> None:
    """Inspect one exact spatial and equality-filtered source subset."""

    context = _context(ctx)
    _execute(
        context,
        "query",
        lambda: context.operations.query(
            layer=layer,
            area=area,
            bbox=bbox,
            geometry=geometry,
            predicate=predicate,
            where=tuple(where or ()),
            fields=tuple(field or ()),
            state=state,
            export_gpkg=export_gpkg,
            replace_export=replace_export,
        ),
    )


@evidence_app.command("delete")
def delete_command(
    ctx: typer.Context,
    yes: bool = typer.Option(False, "--yes", help="Confirm recoverable store removal."),
    expect_state: str = typer.Option(
        ...,
        "--expect-state",
        help="Exact current coverage state required before moving the store.",
    ),
) -> None:
    """Move the store and lock metadata to recoverable workspace trash."""

    context = _context(ctx)
    _execute(
        context,
        "delete",
        lambda: context.operations.delete(
            yes=yes,
            expect_state=expect_state,
        ),
    )


def _context(ctx: typer.Context) -> EvidenceCliContext:
    if not isinstance(ctx.obj, EvidenceCliContext):
        raise RuntimeError("evidence CLI context was not initialised")
    return ctx.obj


def _execute(
    context: EvidenceCliContext,
    command: str,
    operation: Callable[[], dict[str, object]],
) -> None:
    try:
        payload = operation()
    except Exception as error:
        exit_code = 75 if isinstance(error, EvidenceWriterBusy) else 1
        payload = {
            "ok": False,
            "command": command,
            "store": str(context.operations.paths.store),
            "extension_cache": str(context.operations.paths.extension_cache),
            "error": str(error),
            "exit_code": exit_code,
        }
        _emit(context.output_format, payload)
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(exit_code) from None
    _emit(context.output_format, payload)


def _emit(output_format: OutputFormat, payload: dict[str, object]) -> None:
    if output_format == "json":
        typer.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return
    for key, value in payload.items():
        _emit_text(key.replace("_", "-"), value)


def _emit_text(key: str, value: object) -> None:
    if isinstance(value, dict):
        for child, item in value.items():
            _emit_text(f"{key}.{child.replace('_', '-')}", item)
    elif isinstance(value, list):
        typer.echo(f"{key}: {json.dumps(value, sort_keys=True, separators=(',', ':'))}")
    elif isinstance(value, bool):
        typer.echo(f"{key}: {str(value).lower()}")
    elif value is None:
        typer.echo(f"{key}: null")
    else:
        typer.echo(f"{key}: {value}")
