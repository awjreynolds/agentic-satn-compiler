use std::path::PathBuf;

use clap::Parser;
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
    if cli.mode != "mechanical" {
        return Err(satn_rs::SatnError::InvalidInput(format!(
            "unsupported mode {}; only mechanical is available in this foundation",
            cli.mode
        )));
    }
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
    let report = compile_with_progress(
        &cli.config,
        &cli.output,
        CompileOptions {
            origin: cli.origin,
            destination: cli.destination,
        },
        &mut progress,
    )?;
    println!("{}", serde_json::to_string(&report)?);
    Ok(())
}
