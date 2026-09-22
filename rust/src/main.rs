use std::path::PathBuf;

use clap::Parser;
use satn_rs::judgment::{CodexConfig, TypeSafeConfig};
use satn_rs::midend::{MidendConfig, MidendProgress, ProviderSet, replay, run as run_midend};
use satn_rs::{CompileOptions, ProgressEvent, compile_with_progress};

#[derive(Debug, Parser)]
#[command(name = "satn-rs", about = "Native SATN mechanical compiler")]
struct Cli {
    #[arg(long)]
    config: PathBuf,
    #[arg(long)]
    output: PathBuf,
    #[arg(long, default_value = "mechanical")]
    mode: String,
    #[arg(long, default_value = "main")]
    branch: String,
    #[arg(long)]
    history: Option<PathBuf>,
    #[arg(long, default_value = "jev-latest")]
    jev_model: String,
    #[arg(long)]
    specialist_model: Option<String>,
    #[arg(long)]
    specialist_reasoning_effort: Option<String>,
    #[arg(long)]
    allow_provisional: bool,
    #[arg(long)]
    origin: Option<String>,
    #[arg(long)]
    destination: Option<String>,
}

fn main() {
    if let Err(error) = run() {
        eprintln!("satn-rs: {error}");
        std::process::exit(1);
    }
}

fn run() -> satn_rs::Result<()> {
    let cli = Cli::parse();
    if !matches!(
        cli.mode.as_str(),
        "mechanical" | "deterministic" | "live" | "replay"
    ) {
        return Err(satn_rs::SatnError::InvalidInput(format!(
            "unsupported mode {}",
            cli.mode
        )));
    }
    if cli.specialist_model.is_some() != cli.specialist_reasoning_effort.is_some() {
        return Err(satn_rs::SatnError::InvalidInput(
            "--specialist-model and --specialist-reasoning-effort must be supplied together"
                .to_string(),
        ));
    }
    let history = cli
        .history
        .clone()
        .unwrap_or_else(|| cli.output.join("history"));
    let mut progress = |event: ProgressEvent| {
        eprintln!(
            "[satn-rs] stage={} elapsed_ms={} source_corridors={} connections={} candidates={} {}",
            event.stage,
            event.elapsed_ms,
            event.source_inventory_count,
            event.connection_count,
            event.candidate_count,
            event.message
        );
    };
    if cli.mode == "replay" {
        let mut emit = |event: MidendProgress| {
            eprintln!(
                "[satn-rs] stage={} branch={} task={} provider={} status={} elapsed_ms={}",
                event.stage,
                event.branch,
                event.task_id.as_deref().unwrap_or("-"),
                event.provider.as_deref().unwrap_or("-"),
                event.status,
                event.elapsed_ms,
            );
        };
        let result = replay(&history, &cli.branch, &mut emit)
            .map_err(|error| satn_rs::SatnError::InvalidInput(error.to_string()))?;
        std::fs::create_dir_all(&cli.output)?;
        std::fs::write(
            cli.output.join("planning.json"),
            serde_json::to_string_pretty(&result)?,
        )?;
        println!("{}", serde_json::to_string(&result)?);
        return Ok(());
    }
    let report = compile_with_progress(
        &cli.config,
        &cli.output,
        CompileOptions {
            origin: cli.origin,
            destination: cli.destination,
        },
        &mut progress,
    )?;
    if cli.mode == "live" {
        let mut classifier = TypeSafeConfig::from_env(cli.jev_model.clone())
            .map_err(|error| satn_rs::SatnError::InvalidInput(error.to_string()))?;
        let mut specialist = match (
            cli.specialist_model.as_deref(),
            cli.specialist_reasoning_effort.as_deref(),
        ) {
            (Some(model), Some(effort)) => Some(
                CodexConfig::new("codex", model, effort)
                    .map_err(|error| satn_rs::SatnError::InvalidInput(error.to_string()))?,
            ),
            _ => None,
        };
        let mut emit = |event: MidendProgress| {
            eprintln!(
                "[satn-rs] stage={} branch={} task={} provider={} status={} elapsed_ms={}",
                event.stage,
                event.branch,
                event.task_id.as_deref().unwrap_or("-"),
                event.provider.as_deref().unwrap_or("-"),
                event.status,
                event.elapsed_ms,
            );
        };
        let result = run_midend(
            &history,
            report,
            MidendConfig::live(cli.allow_provisional)
                .with_branch(cli.branch)
                .with_jev_model(cli.jev_model),
            ProviderSet {
                classifier: Some(&mut classifier),
                specialist: specialist
                    .as_mut()
                    .map(|provider| provider as &mut dyn satn_rs::midend::SpecialistProvider),
            },
            &mut emit,
        )
        .map_err(|error| satn_rs::SatnError::InvalidInput(error.to_string()))?;
        std::fs::create_dir_all(&cli.output)?;
        std::fs::write(
            cli.output.join("planning.json"),
            serde_json::to_string_pretty(&result)?,
        )?;
        println!("{}", serde_json::to_string(&result)?);
        return Ok(());
    }
    println!("{}", serde_json::to_string(&report)?);
    Ok(())
}
