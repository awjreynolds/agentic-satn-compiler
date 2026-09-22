use std::error::Error;
use std::fs;
use std::path::{Path, PathBuf};

use clap::Parser;
use satn_rs::{CommunityAccess, CompileOptions, ProgressEvent, prepare_with_progress};
use serde::Deserialize;

const JOURNEY_MAP_TEMPLATE: &str = include_str!("../src/journey_map_template.html");

#[derive(Debug, Deserialize)]
struct PlanningDocument {
    community_access: Vec<CommunityAccess>,
}

#[derive(Debug, Parser)]
#[command(
    name = "journey_comparison",
    about = "Compare a rural feeder to an admitted town"
)]
struct Args {
    #[arg(long, value_name = "AREA.YAML")]
    config: PathBuf,
    #[arg(long, value_name = "PLANNING.JSON")]
    planning: PathBuf,
    #[arg(long, value_name = "NAME_OR_ID")]
    origin: String,
    #[arg(long, value_name = "NAME_OR_ID")]
    destination: String,
    #[arg(long, value_name = "ACCESS.JSON")]
    retained_alternative: Option<PathBuf>,
    #[arg(long, value_name = "DIR")]
    output: PathBuf,
    #[arg(long, default_value = "src/satn/assets", value_name = "DIR")]
    asset_root: PathBuf,
}

fn main() -> Result<(), Box<dyn Error>> {
    let args = Args::parse();
    let planning: PlanningDocument = serde_json::from_str(&fs::read_to_string(&args.planning)?)?;
    let selected = find_access(&planning.community_access, &args.origin)?;
    let retained_alternative = args
        .retained_alternative
        .as_ref()
        .map(|path| -> Result<CommunityAccess, Box<dyn Error>> {
            Ok(serde_json::from_str(&fs::read_to_string(path)?)?)
        })
        .transpose()?;

    let mut progress = |event: ProgressEvent| {
        eprintln!(
            "{}: {} ({} ms)",
            event.stage, event.message, event.elapsed_ms
        );
    };
    let prepared = prepare_with_progress(&args.config, CompileOptions::default(), &mut progress)?;
    let comparison = prepared.compare_complete_journey(
        &planning.community_access,
        selected,
        retained_alternative.as_ref(),
        &args.destination,
    )?;

    fs::create_dir_all(&args.output)?;
    fs::write(
        args.output.join("journey-comparison.json"),
        serde_json::to_vec_pretty(&comparison)?,
    )?;
    fs::write(
        args.output.join("journey-comparison.geojson"),
        serde_json::to_vec_pretty(&comparison.to_geojson())?,
    )?;
    write_viewer(&args.output, &args.asset_root)?;

    print_path("selected", &comparison.selected);
    if let Some(path) = &comparison.retained_alternative {
        print_path("retained-alternative", path);
    }
    print_path("direct", &comparison.direct);
    println!(
        "selected_network_status={}",
        comparison.selected.network_status
    );
    println!("output={}", args.output.display());
    Ok(())
}

fn find_access<'a>(
    records: &'a [CommunityAccess],
    name_or_id: &str,
) -> Result<&'a CommunityAccess, Box<dyn Error>> {
    let matches = records
        .iter()
        .filter(|access| {
            access.is_primary && (access.community_id == name_or_id || access.name == name_or_id)
        })
        .collect::<Vec<_>>();
    match matches.as_slice() {
        [access] => Ok(access),
        [] => {
            Err(format!("primary community {name_or_id:?} was not found in planning JSON").into())
        }
        _ => Err(format!("primary community {name_or_id:?} is ambiguous").into()),
    }
}

fn print_path(label: &str, path: &satn_rs::JourneyPath) {
    let profile = &path.topography;
    let gradient = profile
        .sustained_gradient
        .as_ref()
        .map(|value| value.gradient_pct);
    println!(
        "{label}: length_m={:.3} new_link_m={:?} feeder_m={:?} shared_suffix_m={:?} onward_m={:?} ascent_m={:?} variation_m={:?} gradient_pct={:?} terrain={:?} samples={} max_gap_m={:?}",
        path.length_m,
        path.new_link_length_m,
        path.feeder_length_m,
        path.shared_suffix_length_m,
        path.onward_length_m,
        profile.forward_ascent_m,
        profile.cumulative_elevation_variation_m,
        gradient,
        profile.availability,
        profile.coverage.sample_count,
        profile.coverage.maximum_gap_m,
    );
}

fn write_viewer(output: &Path, asset_root: &Path) -> Result<(), Box<dyn Error>> {
    let assets = output.join("assets");
    fs::create_dir_all(&assets)?;
    fs::write(output.join("index.html"), JOURNEY_MAP_TEMPLATE)?;
    for name in ["maplibre-gl.css", "maplibre-gl.js", "MAPLIBRE-LICENSE.txt"] {
        fs::copy(asset_root.join(name), assets.join(name))?;
    }
    Ok(())
}
