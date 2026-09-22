use std::path::Path;

use serde_json::{Value, json};

use crate::compiler::{Candidate, CompileReport};
use crate::error::Result;

pub(crate) fn write_bundle(output_dir: &Path, report: &CompileReport) -> Result<()> {
    std::fs::create_dir_all(output_dir)?;
    std::fs::write(
        output_dir.join("summary.json"),
        serde_json::to_string_pretty(report)?,
    )?;
    std::fs::write(
        output_dir.join("network.geojson"),
        serde_json::to_string_pretty(&network_geojson(report))?,
    )?;
    std::fs::write(output_dir.join("index.html"), render_html(report))?;
    Ok(())
}

fn network_geojson(report: &CompileReport) -> Value {
    let mut features = Vec::new();
    if let Some(boundary) = &report.boundary_scope {
        features.push(json!({
            "type": "Feature",
            "properties": {
                "kind": "boundary-scope",
                "boundary_id": boundary.id,
                "name": boundary.name
            },
            "geometry": {"type": "MultiPolygon", "coordinates": boundary.geometry}
        }));
    }
    for source in &report.source_inventory {
        for coordinates in &source.geometry {
            features.push(json!({
                "type": "Feature",
                "properties": {
                    "kind": "source-baseline",
                    "source_kind": source.source_kind,
                    "source_corridor_ref": source.reference,
                    "source_id": source.source_id,
                    "scope": source.scope,
                    "topology_status": source.topology_status,
                    "attachment_status": source.attachment_status
                },
                "geometry": {"type": "LineString", "coordinates": coordinates}
            }));
        }
    }
    for candidate in &report.candidates {
        features.push(candidate_feature(candidate));
    }
    json!({"type":"FeatureCollection","features":features})
}

fn candidate_feature(candidate: &Candidate) -> Value {
    json!({
        "type": "Feature",
        "properties": {
            "kind": candidate.status,
            "decision_class": candidate.decision_class,
            "candidate_id": candidate.id,
            "connection_id": candidate.connection_id,
            "role": candidate.role,
            "role_aliases": candidate.role_aliases,
            "length_m": candidate.length_m,
            "search_cost_m": candidate.search_cost_m,
            "a_road_share": candidate.a_road_share,
            "ncn_share": candidate.ncn_share,
            "cycle_alignment_bases": candidate.cycle_alignment_bases,
            "topology_status": candidate.topology_status,
            "provision_status": candidate.provision_status
        },
        "geometry": {"type": "LineString", "coordinates": candidate.geometry}
    })
}

fn render_html(report: &CompileReport) -> String {
    let title = html_escape(&report.title);
    let svg = render_svg(report);
    let summary = serde_json::to_string(report).unwrap_or_else(|_| "{}".to_string());
    format!(
        r#"<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
body{{font:16px system-ui,sans-serif;margin:1.5rem;max-width:90rem;color:#17202a}}
svg{{width:100%;height:65vh;min-height:24rem;background:#f6f8fa;border:1px solid #c9d1d9}}
.boundary{{fill:#dbeafe;stroke:#2563eb;stroke-width:1;opacity:.35}}
.source{{fill:none;stroke:#4b5563;stroke-width:1.8;opacity:.72}}
.candidate{{fill:none;stroke:#16803c;stroke-width:3}}
.key{{display:flex;gap:1rem;list-style:none;padding:0;flex-wrap:wrap}}
.swatch{{display:inline-block;width:2rem;height:.35rem;vertical-align:middle;margin-right:.35rem}}
.boundary-key{{background:#2563eb}} .source-key{{background:#4b5563}} .candidate-key{{background:#16803c}}
pre{{max-height:20rem;overflow:auto;background:#f6f8fa;padding:1rem}}
</style></head>
<body><h1>{title}</h1>
<p>Mechanical baseline and source-supported candidates. Provision, safety, access and adoption remain explicit unknowns until separately evidenced.</p>
<ul class="key"><li><span class="swatch boundary-key"></span>Governed boundary</li><li><span class="swatch source-key"></span>Source A-road inventory</li><li><span class="swatch candidate-key"></span>Mechanical candidate</li></ul>
<p>{source_count} source corridors · {connection_count} prepared connections · {candidate_count} generated candidates · {unknown_count} unresolved facts</p>
{svg}
<details><summary>Compact mechanical report</summary><pre id="summary"></pre></details>
<script>const report={summary};document.querySelector('#summary').textContent=JSON.stringify(report,null,2);</script>
</body></html>"#,
        title = title,
        source_count = report.source_inventory_count,
        connection_count = report.connection_count,
        candidate_count = report.candidate_count,
        unknown_count = report.unknown_fact_count,
        svg = svg,
        summary = summary,
    )
}

fn render_svg(report: &CompileReport) -> String {
    let mut coordinates = Vec::new();
    if let Some(boundary) = &report.boundary_scope {
        coordinates.extend(boundary.geometry.iter().flatten().flatten().copied());
    }
    for source in &report.source_inventory {
        coordinates.extend(source.geometry.iter().flatten().copied());
    }
    for candidate in &report.candidates {
        coordinates.extend(candidate.geometry.iter().copied());
    }
    if coordinates.is_empty() {
        return "<svg viewBox=\"0 0 1000 600\" role=\"img\" aria-label=\"Empty planning map\"><text x=\"20\" y=\"40\">No geometry admitted</text></svg>".to_string();
    }
    let (min_x, max_x) = min_max(coordinates.iter().map(|point| point[0]));
    let (min_y, max_y) = min_max(coordinates.iter().map(|point| point[1]));
    let width = 1000.0;
    let height = 600.0;
    let pad = 24.0;
    let scale_x = (width - 2.0 * pad) / (max_x - min_x).max(0.000001);
    let scale_y = (height - 2.0 * pad) / (max_y - min_y).max(0.000001);
    let scale = scale_x.min(scale_y);
    let project = |point: [f64; 2]| {
        let x = pad + (point[0] - min_x) * scale;
        let y = height - pad - (point[1] - min_y) * scale;
        format!("{x:.2},{y:.2}")
    };
    let mut elements = Vec::new();
    if let Some(boundary) = &report.boundary_scope {
        for polygon in &boundary.geometry {
            let mut path = String::new();
            for ring in polygon {
                let Some(first) = ring.first().copied() else {
                    continue;
                };
                path.push_str(&format!("M {} ", project(first)));
                for point in ring.iter().skip(1).copied() {
                    path.push_str(&format!("L {} ", project(point)));
                }
                path.push_str("Z ");
            }
            elements.push(format!(
                "<path class=\"boundary\" fill-rule=\"evenodd\" d=\"{}\" />",
                path.trim()
            ));
        }
    }
    for source in &report.source_inventory {
        for line in &source.geometry {
            elements.push(format!(
                "<polyline class=\"source\" points=\"{}\" aria-label=\"{} source {}\" />",
                line.iter()
                    .copied()
                    .map(project)
                    .collect::<Vec<_>>()
                    .join(" "),
                html_escape(&source.source_kind),
                html_escape(&source.reference)
            ));
        }
    }
    for candidate in &report.candidates {
        elements.push(format!(
            "<polyline class=\"candidate\" points=\"{}\" aria-label=\"{}\" />",
            candidate
                .geometry
                .iter()
                .copied()
                .map(project)
                .collect::<Vec<_>>()
                .join(" "),
            html_escape(&candidate.id)
        ));
    }
    format!(
        "<svg viewBox=\"0 0 {width} {height}\" role=\"img\" aria-label=\"Mechanical planning review map\">{}</svg>",
        elements.join("")
    )
}

fn min_max(values: impl Iterator<Item = f64>) -> (f64, f64) {
    values.fold((f64::INFINITY, f64::NEG_INFINITY), |(min, max), value| {
        (min.min(value), max.max(value))
    })
}

fn html_escape(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
}
