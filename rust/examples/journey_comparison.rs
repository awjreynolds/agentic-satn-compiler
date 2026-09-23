use std::error::Error;
use std::fs;
use std::path::{Path, PathBuf};

use clap::Parser;
use satn_rs::{
    CommunityAccess, CompileOptions, JourneyBatchEvaluation, JourneyPairStatus, ProgressEvent,
    prepare_with_progress,
};
use serde::Deserialize;
use serde_json::Value;

const JOURNEY_MAP_TEMPLATE: &str = include_str!("../src/journey_map_template.html");
const JOURNEY_INDEX_TEMPLATE: &str = include_str!("../src/journey_index_template.html");

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
    #[arg(
        long,
        help = "Evaluate every retained primary against every admitted town/city"
    )]
    batch: bool,
    #[arg(long, value_name = "AREA.YAML")]
    config: PathBuf,
    #[arg(long, value_name = "PLANNING.JSON")]
    planning: PathBuf,
    #[arg(long, value_name = "NAME_OR_ID")]
    origin: Option<String>,
    #[arg(long, value_name = "NAME_OR_ID")]
    destination: Option<String>,
    #[arg(
        long,
        value_name = "ACCESS.JSON",
        help = "One retained access or a JSON array/document of retained accesses"
    )]
    retained_alternative: Option<PathBuf>,
    #[arg(long, value_name = "DIR")]
    output: PathBuf,
    #[arg(long, default_value = "src/satn/assets", value_name = "DIR")]
    asset_root: PathBuf,
}

fn main() -> Result<(), Box<dyn Error>> {
    let args = Args::parse();
    let planning: PlanningDocument = serde_json::from_str(&fs::read_to_string(&args.planning)?)?;
    let retained_alternatives = args
        .retained_alternative
        .as_ref()
        .map(|path| read_access_records(path))
        .transpose()?
        .unwrap_or_default();

    let mut progress = |event: ProgressEvent| {
        eprintln!(
            "{}: {} ({} ms)",
            event.stage, event.message, event.elapsed_ms
        );
    };
    let prepared = prepare_with_progress(&args.config, CompileOptions::default(), &mut progress)?;

    if args.batch {
        if args.origin.is_some() || args.destination.is_some() {
            return Err("--batch cannot be combined with --origin or --destination".into());
        }
        let batch = prepared.compare_all_journeys_with_progress(
            &planning.community_access,
            &retained_alternatives,
            &mut |event| {
                eprintln!(
                    "journey-batch: {}/{} {} -> {} {}",
                    event.completed_pair_count,
                    event.total_pair_count,
                    event.origin_name,
                    event.destination_name,
                    status_label(event.status),
                );
            },
        );
        write_batch(&args.output, &args.asset_root, batch)?;
        return Ok(());
    }

    let origin = args
        .origin
        .as_deref()
        .ok_or("--origin is required unless --batch is set")?;
    let destination = args
        .destination
        .as_deref()
        .ok_or("--destination is required unless --batch is set")?;
    let selected = find_access(&planning.community_access, origin)?;
    let retained_alternative = retained_alternatives
        .iter()
        .find(|access| access.community_id == selected.community_id);
    if args.retained_alternative.is_some() && retained_alternative.is_none() {
        return Err(format!(
            "retained alternatives contain no access for origin {:?}",
            selected.community_id
        )
        .into());
    }
    let comparison = prepared.compare_complete_journey(
        &planning.community_access,
        selected,
        retained_alternative,
        destination,
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

fn read_access_records(path: &Path) -> Result<Vec<CommunityAccess>, Box<dyn Error>> {
    let value: Value = serde_json::from_str(&fs::read_to_string(path)?)?;
    if let Some(records) = value.get("community_access") {
        return Ok(serde_json::from_value(records.clone())?);
    }
    if let Some(records) = value.get("records").and_then(Value::as_array) {
        return records
            .iter()
            .map(|record| {
                let access = record.get("access").unwrap_or(record);
                Ok(serde_json::from_value(access.clone())?)
            })
            .collect();
    }
    if value.is_array() {
        return Ok(serde_json::from_value(value)?);
    }
    Ok(vec![serde_json::from_value(value)?])
}

fn write_batch(
    output: &Path,
    asset_root: &Path,
    mut batch: JourneyBatchEvaluation,
) -> Result<(), Box<dyn Error>> {
    let pairs_dir = output.join("pairs");
    fs::create_dir_all(&pairs_dir)?;
    fs::create_dir_all(output)?;
    fs::write(output.join("index.html"), JOURNEY_INDEX_TEMPLATE)?;
    fs::write(output.join("journey-map.html"), JOURNEY_MAP_TEMPLATE)?;
    for (index, success) in batch.successes.iter().enumerate() {
        let stem = format!("pair-{index}-{}", safe_component(&success.pair_id));
        let pair_dir = pairs_dir.join(&stem);
        fs::create_dir_all(&pair_dir)?;
        fs::write(
            pair_dir.join("journey-comparison.json"),
            serde_json::to_vec_pretty(&success.comparison)?,
        )?;
        fs::write(
            pair_dir.join("journey-comparison.geojson"),
            serde_json::to_vec_pretty(&success.comparison.to_geojson())?,
        )?;
        if let Some(pair) = batch
            .summary
            .pairs
            .iter_mut()
            .find(|pair| pair.pair_id == success.pair_id)
        {
            pair.artifact_stem = Some(format!("pairs/{stem}"));
        }
    }
    fs::write(
        output.join("journey-batch-summary.json"),
        serde_json::to_vec_pretty(&batch.summary)?,
    )?;
    write_assets(output, asset_root)?;
    println!(
        "batch: {} pairs, {} success, {} unsupported, {} error; output={}",
        batch.summary.pair_count,
        batch.summary.success_count,
        batch.summary.unsupported_count,
        batch.summary.error_count,
        output.display()
    );
    Ok(())
}

fn status_label(status: JourneyPairStatus) -> &'static str {
    match status {
        JourneyPairStatus::Success => "success",
        JourneyPairStatus::Unsupported => "unsupported",
        JourneyPairStatus::Error => "error",
    }
}

fn safe_component(value: &str) -> String {
    let component = value
        .chars()
        .map(|character| {
            if character.is_ascii_alphanumeric() || character == '-' || character == '_' {
                character
            } else {
                '_'
            }
        })
        .collect::<String>();
    if component.is_empty() {
        "pair".to_string()
    } else {
        component
    }
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
    fs::write(output.join("index.html"), JOURNEY_MAP_TEMPLATE)?;
    write_assets(output, asset_root)
}

fn write_assets(output: &Path, asset_root: &Path) -> Result<(), Box<dyn Error>> {
    let assets = output.join("assets");
    fs::create_dir_all(&assets)?;
    for name in ["maplibre-gl.css", "maplibre-gl.js", "MAPLIBRE-LICENSE.txt"] {
        fs::copy(asset_root.join(name), assets.join(name))?;
    }
    Ok(())
}
