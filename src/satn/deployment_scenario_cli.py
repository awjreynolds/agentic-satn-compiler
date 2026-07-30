"""CLI commands for asynchronous officer request and scenario deployment seams."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Annotated

import typer

from satn.deployment_scenarios import (
    DeploymentScenarioConfiguration,
    compile_clean_baseline_deployment,
    compile_named_officer_scenario,
    export_response_packet,
    import_response_into_register,
    list_actionable_requests,
    load_canonical_ledger,
    parse_request_register,
)
from satn.models import AreaDefinition
from satn.officer_decisions import CleanSATNBaseline, HumanInterventionResponse

scenario_app = typer.Typer(
    no_args_is_help=True,
    help="Manage non-waiting officer requests and deployment scenarios.",
)
requests_app = typer.Typer(no_args_is_help=True)
ledger_app = typer.Typer(no_args_is_help=True)
scenario_app.add_typer(requests_app, name="requests")
scenario_app.add_typer(ledger_app, name="ledger")


@requests_app.command("list")
def list_requests_command(register: Path) -> None:
    """List every request and its inspectable lifecycle state."""

    requests = parse_request_register(register.read_bytes())
    typer.echo(json.dumps(list_actionable_requests(requests), indent=2))


@requests_app.command("export")
def export_request_command(
    register: Path,
    request_id: str,
    output: Annotated[Path, typer.Option("--output", "-o")],
) -> None:
    """Export one exact pending request as a finite response packet."""

    requests = parse_request_register(register.read_bytes())
    packet = export_response_packet(requests, request_id)
    output.write_bytes(
        json.dumps(
            packet.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
    )
    typer.echo(output)


@ledger_app.command("validate")
def validate_ledger_command(ledger: Path) -> None:
    """Validate exact canonical Officer Decision Ledger bytes."""

    parsed = load_canonical_ledger(ledger)
    typer.echo(parsed.ledger_fingerprint)


@ledger_app.command("import")
def import_response_command(
    register: Path,
    response: Path,
    ledger: Path,
    output_register: Annotated[Path, typer.Option("--output-register")],
    output_ledger: Annotated[Path, typer.Option("--output-ledger")],
) -> None:
    """Import one response into the request register and sole officer ledger."""

    requests = parse_request_register(register.read_bytes())
    parsed_response = HumanInterventionResponse.model_validate_json(
        response.read_text(encoding="ascii")
    )
    parsed_ledger = load_canonical_ledger(ledger)
    updated_requests, updated_ledger = import_response_into_register(
        requests,
        parsed_response,
        parsed_ledger,
    )
    output_register.write_bytes(updated_requests.canonical_json())
    output_ledger.write_bytes(updated_ledger.canonical_json())
    typer.echo(updated_ledger.ledger_fingerprint)


@scenario_app.command("compile")
def compile_scenario_command(
    scenario_config: Path,
    area_config: Path,
    baseline: Path,
    output: Annotated[Path, typer.Option("--output", "-o")],
    scenario_name: Annotated[str, typer.Option("--scenario")] = "clean-baseline",
    ledger: Annotated[Path | None, typer.Option("--ledger")] = None,
    effective_on: Annotated[str, typer.Option("--effective-on")] = date.today().isoformat(),
) -> None:
    """Compile deployment metadata; never waits for a human or regenerates maps."""

    configuration = DeploymentScenarioConfiguration.from_yaml(scenario_config)
    area = AreaDefinition.from_yaml(area_config)
    try:
        effective_date = date.fromisoformat(effective_on)
    except ValueError as error:
        raise typer.BadParameter("--effective-on must be an ISO date") from error
    if configuration.deployment_id != area.deployment_slug:
        raise typer.BadParameter(
            "scenario configuration deployment_id differs from Area Definition"
        )
    clean = CleanSATNBaseline.model_validate_json(baseline.read_text(encoding="ascii"))
    if scenario_name == configuration.clean_baseline.name:
        publication = compile_clean_baseline_deployment(
            configuration,
            clean,
            runtime_provider=area.compilation.agent.provider,
            effective_on=effective_date,
        )
    else:
        deployment = configuration.named_scenario(scenario_name)
        ledger_path = ledger or configuration.resolve(deployment.ledger_path)
        publication = compile_named_officer_scenario(
            configuration,
            clean,
            load_canonical_ledger(ledger_path),
            scenario_name,
            runtime_provider=area.compilation.agent.provider,
            effective_on=effective_date,
        )
    output.write_text(
        publication.model_dump_json(indent=2),
        encoding="utf-8",
    )
    typer.echo(output)
