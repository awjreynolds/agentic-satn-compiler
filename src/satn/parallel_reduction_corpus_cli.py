"""Explicit expected-result regeneration for the Parallel-Reduction corpus."""

from __future__ import annotations

from pathlib import Path

import typer

from satn.parallel_reduction_corpus import (
    ScriptedCorpusRuntime,
    canonical_expected_result,
    load_manifest,
    write_expected_result,
    write_expected_visual,
)

corpus_app = typer.Typer(no_args_is_help=True, help="Manage governed synthetic corpora.")
parallel_reduction_app = typer.Typer(no_args_is_help=True)
corpus_app.add_typer(parallel_reduction_app, name="parallel-reduction")


@parallel_reduction_app.command("regenerate")
def regenerate_parallel_reduction_expected_result(manifest_path: Path) -> None:
    """Explicitly propose a canonical expected result; CI never invokes this."""

    from satn.parallel_reduction import (
        ParallelReductionRequest,
        compile_parallel_reduction_scenario,
    )

    manifest = load_manifest(manifest_path)
    result = compile_parallel_reduction_scenario(
        ParallelReductionRequest.model_validate(manifest.request),
        runtime=ScriptedCorpusRuntime(manifest.runtime_responses),
    )
    if not result.scenario.publishable:
        raise typer.BadParameter(
            "corpus compilation did not produce a complete Scenario Compilation"
        )
    expected = canonical_expected_result(manifest, result)
    write_expected_result(manifest.expected_result_path, expected)
    write_expected_visual(manifest.expected_visual_path, expected)
    typer.echo(manifest.expected_result_path)
    typer.echo(manifest.expected_visual_path)
