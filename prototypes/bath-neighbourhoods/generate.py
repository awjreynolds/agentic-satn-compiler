#!/usr/bin/env python3
# ruff: noqa: E501  # Preserve long lines in the embedded standalone HTML/CSS/JS template.
"""Throwaway generator for the Bath classified-road neighbourhood review."""

from __future__ import annotations

import argparse
import json
import pathlib
import time
from collections import Counter

from pyproj import Transformer
from shapely.geometry import LineString, Point, shape
from shapely.ops import polygonize, transform, unary_union
from shapely.strtree import STRtree

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]
OUTPUT = HERE / "index.html"

CLASSES = {
    "a-road": "A Road",
    "b-road": "B Road",
    "classified-unnumbered": "Classified Unnumbered",
}
TRANSFORM = Transformer.from_crs("EPSG:4326", "EPSG:27700", always_xy=True).transform


def load_geojson(path: pathlib.Path) -> dict:
    with path.open(encoding="utf-8") as source:
        return json.load(source)


def normalized_key(geometry) -> tuple:
    normalized = geometry.normalize()
    return (*tuple(round(value, 9) for value in geometry.bounds), normalized.wkb_hex)


def line_parts(geometry):
    if geometry.geom_type == "LineString":
        yield geometry
    elif hasattr(geometry, "geoms"):
        for part in geometry.geoms:
            yield from line_parts(part)


def projected_length(geometry) -> float:
    return transform(TRANSFORM, geometry).length


def svg_path(geometry) -> str:
    """Encode complete projected geometry for display; no geometry is clipped."""

    commands: list[str] = []

    def ring(coords, close: bool) -> None:
        if not coords:
            return
        x, y = TRANSFORM(coords[0][0], coords[0][1])
        commands.append(f"M{x:.1f},{-y:.1f}")
        for lon, lat, *_ in coords[1:]:
            x, y = TRANSFORM(lon, lat)
            commands.append(f"L{x:.1f},{-y:.1f}")
        if close:
            commands.append("Z")

    def walk(item) -> None:
        if item.geom_type == "Polygon":
            ring(item.exterior.coords, True)
            for interior in item.interiors:
                ring(interior.coords, True)
        elif item.geom_type == "LineString":
            ring(item.coords, False)
        elif hasattr(item, "geoms"):
            for part in item.geoms:
                walk(part)

    walk(geometry)
    return " ".join(commands)


def point_xy(coordinates) -> list[float]:
    x, y = TRANSFORM(coordinates[0], coordinates[1])
    return [round(x, 1), round(-y, 1)]


def geometry_intersection_intervals(route, candidate, candidate_id: str) -> list[dict]:
    overlap = route.intersection(candidate)
    intervals = []
    for part in line_parts(overlap):
        if part.length <= 0:
            continue
        start = route.project(Point(part.coords[0]))
        end = route.project(Point(part.coords[-1]))
        intervals.append(
            {
                "candidate_id": candidate_id,
                "start_m": round(min(start, end), 1),
                "end_m": round(max(start, end), 1),
                "length_m": round(part.length, 1),
            }
        )
    return intervals


def make_scene(repo_root: pathlib.Path) -> tuple[dict, dict]:
    snapshot = repo_root / "data/snapshots/banes-osm-open-roads-v1-2026-07-29"
    corrected = repo_root / "build/rust-rebuild/urban-entry-banes-corrected"
    roads = load_geojson(snapshot / "official-road-classification.geojson")["features"]
    classified = [
        feature
        for feature in roads
        if feature.get("properties", {}).get("official_classification") in CLASSES
    ]
    road_geometries = [shape(feature["geometry"]) for feature in classified]

    # Polygonize the complete selected source linework before considering Bath.
    full_polygons = sorted(polygonize(unary_union(road_geometries)), key=normalized_key)

    place_features = load_geojson(snapshot / "osm-place-features.geojson")["features"]
    bath_feature = next(
        feature
        for feature in place_features
        if str(feature.get("properties", {}).get("id")) == "5342409"
        and feature.get("properties", {}).get("name") == "Bath"
        and feature.get("geometry", {}).get("type") in {"Polygon", "MultiPolygon"}
    )
    bath_geometry = shape(bath_feature["geometry"])

    # Filter by intersection only. Retained polygons remain complete and unchanged.
    candidates = sorted(
        (polygon for polygon in full_polygons if polygon.intersects(bath_geometry)),
        key=normalized_key,
    )
    projected_candidates = [transform(TRANSFORM, polygon) for polygon in candidates]

    tree = STRtree(road_geometries)
    candidate_rows = []
    boundary_road_indices: set[int] = set()
    for index, (polygon, projected) in enumerate(
        zip(candidates, projected_candidates, strict=True), 1
    ):
        candidate_id = f"bath-candidate-{index:03d}"
        boundary = polygon.boundary
        source_rows = []
        for road_index in tree.query(boundary, predicate="intersects"):
            shared_length = road_geometries[int(road_index)].intersection(boundary).length
            if shared_length <= 1e-12:
                continue
            feature = classified[int(road_index)]
            properties = feature["properties"]
            boundary_road_indices.add(int(road_index))
            source_rows.append(
                {
                    "official_feature_id": properties.get("official_feature_id"),
                    "classification": CLASSES[properties["official_classification"]],
                    "road_number": properties.get("official_road_number"),
                    "road_name": properties.get("official_road_name"),
                    "road_function": properties.get("official_road_function"),
                }
            )
        source_rows.sort(
            key=lambda item: (
                item["classification"],
                item["official_feature_id"] or "",
            )
        )
        class_counts = Counter(row["classification"] for row in source_rows)
        min_x, min_y, max_x, max_y = projected.bounds
        candidate_rows.append(
            {
                "id": candidate_id,
                "path": svg_path(polygon),
                "area_ha": round(projected.area / 10_000, 4),
                "source_count": len(source_rows),
                "class_counts": dict(class_counts),
                "sources": source_rows,
                "bounds": [round(min_x, 1), round(-max_y, 1), round(max_x, 1), round(-min_y, 1)],
            }
        )

    # Show full source road features touching Bath or forming a shown candidate boundary.
    view_road_indices = set(
        int(value) for value in tree.query(bath_geometry, predicate="intersects")
    )
    view_road_indices.update(boundary_road_indices)
    road_rows = []
    for road_index in sorted(view_road_indices):
        feature = classified[road_index]
        properties = feature["properties"]
        geometry = road_geometries[road_index]
        road_rows.append(
            {
                "path": svg_path(geometry),
                "id": properties.get("official_feature_id"),
                "classification": CLASSES[properties["official_classification"]],
                "road_number": properties.get("official_road_number"),
                "road_name": properties.get("official_road_name"),
                "road_function": properties.get("official_road_function"),
                "source_id": properties.get("source_id"),
                "effective_date": properties.get("effective_date"),
                "licence": properties.get("licence"),
            }
        )

    plan = load_geojson(corrected / "planning.json")
    entries = []
    for access in plan["community_access"]:
        entry = access.get("urban_entry") or {}
        if entry.get("destination_name") != "Bath" or not access.get("path_geometry"):
            continue
        route = transform(TRANSFORM, LineString(access["path_geometry"]))
        hits = []
        for candidate_id, projected in zip(
            (row["id"] for row in candidate_rows), projected_candidates, strict=True
        ):
            hits.extend(geometry_intersection_intervals(route, projected, candidate_id))
        hits.sort(key=lambda item: (item["start_m"], item["end_m"], item["candidate_id"]))
        projected_route = transform(TRANSFORM, LineString(access["path_geometry"]))
        min_x, min_y, max_x, max_y = projected_route.bounds
        path_pad = max(max_x - min_x, max_y - min_y) * 0.08
        entries.append(
            {
                "community_id": access["community_id"],
                "name": access["name"],
                "source_id": access.get("source_id"),
                "path": svg_path(LineString(access["path_geometry"])),
                "path_length_m": round(projected_route.length, 1),
                "bounds": [
                    round(min_x - path_pad, 1),
                    round(-max_y - path_pad, 1),
                    round(max_x + path_pad, 1),
                    round(-min_y + path_pad, 1),
                ],
                "start": point_xy(access["path_geometry"][0]),
                "crossing": point_xy(entry["point"]),
                "crossing_edge_id": entry.get("edge_id"),
                "crossing_fraction": entry.get("fraction"),
                "extent_source_id": entry.get("extent_source_id"),
                "path_edge_ids": access.get("path_edge_ids", []),
                "intersections": hits,
            }
        )
    entries.sort(key=lambda row: row["name"])

    projected_bath = transform(TRANSFORM, bath_geometry)
    min_x, min_y, max_x, max_y = projected_bath.bounds
    margin_x = (max_x - min_x) * 0.06
    margin_y = (max_y - min_y) * 0.06
    bath_view = [
        round(min_x - margin_x, 1),
        round(-max_y - margin_y, 1),
        round(max_x + margin_x, 1),
        round(-min_y + margin_y, 1),
    ]

    all_bounds = [row["bounds"] for row in candidate_rows]
    all_bounds.append(bath_view)
    all_bounds.extend(
        [
            min(row["start"][0], row["crossing"][0]),
            min(row["start"][1], row["crossing"][1]),
            max(row["start"][0], row["crossing"][0]),
            max(row["start"][1], row["crossing"][1]),
        ]
        for row in entries
    )
    all_view = [
        min(row[0] for row in all_bounds),
        min(row[1] for row in all_bounds),
        max(row[2] for row in all_bounds),
        max(row[3] for row in all_bounds),
    ]
    span = max(all_view[2] - all_view[0], all_view[3] - all_view[1])
    all_pad = span * 0.04
    all_view = [
        round(all_view[0] - all_pad, 1),
        round(all_view[1] - all_pad, 1),
        round(all_view[2] + all_pad, 1),
        round(all_view[3] + all_pad, 1),
    ]

    counts = {
        "official_lines_used": len(classified),
        "full_polygon_count": len(full_polygons),
        "bath_candidates": len(candidate_rows),
        "local_classified_lines": len(road_rows),
        "retained_bath_entries": len(entries),
        "entry_path_polygon_intersections": sum(len(row["intersections"]) for row in entries),
    }
    scene = {
        "counts": counts,
        "bath": {
            "path": svg_path(bath_geometry),
            "source_id": "5342409",
            "name": "Bath",
            "place": bath_feature.get("properties", {}).get("place"),
            "boundary": bath_feature.get("properties", {}).get("boundary"),
            "wikidata": bath_feature.get("properties", {}).get("wikidata"),
        },
        "polygons": candidate_rows,
        "roads": road_rows,
        "entries": entries,
        "bathView": bath_view,
        "allView": all_view,
        "disclaimer": "Planar closure from official classified-road geometry is a candidate only. It does not establish an existing low-traffic neighbourhood, safe crossing, access permission, or cycle provision.",
        "onward_route_status": "No retained onward urban route from the entry crossing through Bath is present in the corrected urban-entry artifact.",
    }
    return scene, {
        "official_lines_used": len(classified),
        "full_polygon_count": len(full_polygons),
        "bath_candidates": len(candidate_rows),
        "local_classified_lines": len(road_rows),
        "retained_bath_entries": len(entries),
        "entry_path_polygon_intersections": counts["entry_path_polygon_intersections"],
    }


HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bath classified-road neighbourhood candidates</title>
<style>
:root{font:14px/1.45 ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#19242a;background:#f2f4f3}
*{box-sizing:border-box}body{margin:0}header{padding:18px 22px 14px;background:#fff;border-bottom:1px solid #d9e0dd}
h1{font-size:22px;line-height:1.2;margin:0 0 5px}header p{margin:0;color:#53615f;max-width:1050px}
.tag{display:inline-block;margin-left:8px;border-radius:999px;background:#fff0c9;color:#604813;padding:3px 8px;font-size:11px;font-weight:800;letter-spacing:.04em;vertical-align:3px}
.counts{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}.count{background:#f2f6f4;border:1px solid #dce6e1;border-radius:8px;padding:7px 10px;color:#4b5b57}.count strong{color:#162a25;font-size:16px;margin-right:4px}
.layout{display:grid;grid-template-columns:320px 1fr;gap:12px;padding:12px;min-height:calc(100vh - 124px)}
aside,.map-shell{background:white;border:1px solid #d9e0dd;border-radius:10px;overflow:hidden}.side{padding:14px}.side h2{font-size:15px;margin:0 0 8px}.side p{margin:6px 0;color:#56635f}.controls{display:grid;gap:8px;margin:10px 0 14px}.toggle{display:flex;gap:8px;align-items:center;color:#263834}.toggle input{accent-color:#315f54}
.buttons{display:flex;gap:7px;flex-wrap:wrap;margin:11px 0}.buttons button,.entry-card{border:1px solid #d5dfdb;background:#f8faf9;color:#263834;border-radius:7px;padding:7px 9px;cursor:pointer}.buttons button:hover,.entry-card:hover{background:#edf4f0}
.entry-list{display:grid;gap:6px;margin:8px 0 14px}.entry-card{text-align:left;width:100%}.entry-card strong{display:block}.entry-card small{color:#5f6d68}
.detail{border-top:1px solid #e2e8e5;padding-top:10px;margin-top:12px;max-height:42vh;overflow:auto}.detail h3{font-size:14px;margin:0 0 6px}.detail p{margin:5px 0}.detail ul{padding-left:18px;margin:5px 0}.detail li{margin:4px 0}.muted{font-size:12px;color:#66746f}.warning{background:#fff7e6;border-left:3px solid #d79726;padding:8px 10px;border-radius:4px;color:#59451c}
.map-shell{position:relative;min-height:650px}.map-top{position:absolute;z-index:2;left:10px;right:10px;top:9px;display:flex;justify-content:space-between;pointer-events:none}.map-top span{background:#fffffff0;border:1px solid #dce4e0;border-radius:6px;padding:5px 8px;color:#50605a;font-size:12px;pointer-events:auto}
svg{display:block;width:100%;height:calc(100vh - 150px);min-height:650px;background:#f8faf8;touch-action:none;cursor:grab}svg.dragging{cursor:grabbing}
.bath-boundary{fill:#e7f1f3;fill-opacity:.25;stroke:#3b7a86;stroke-width:3;stroke-dasharray:8 5;vector-effect:non-scaling-stroke;pointer-events:none}
.candidate{stroke:#295b4c;stroke-width:1.8;fill:#9ac6a7;fill-opacity:.38;vector-effect:non-scaling-stroke;cursor:pointer}.candidate:hover{fill-opacity:.62;stroke-width:3}
.road{fill:none;stroke-width:2.4;vector-effect:non-scaling-stroke;cursor:pointer}.road:hover{stroke-width:4}.road-a{stroke:#b84e37}.road-b{stroke:#6b52a3}.road-unnumbered{stroke:#d1942d}
.approach-hit{fill:none;stroke:#193f9b;stroke-width:9;stroke-linecap:round;stroke-linejoin:round;opacity:.86;vector-effect:non-scaling-stroke;pointer-events:none}
.approach{fill:none;stroke:#325fd0;stroke-width:3.6;stroke-dasharray:10 5;stroke-linecap:round;stroke-linejoin:round;vector-effect:non-scaling-stroke;cursor:pointer}.approach:hover{stroke-width:5}
.entry-point{fill:#fff;stroke:#173d8e;stroke-width:3;vector-effect:non-scaling-stroke;cursor:pointer}.entry-start{fill:#325fd0;stroke:white;stroke-width:1.5;vector-effect:non-scaling-stroke}.hidden{display:none}.key{display:flex;gap:12px;flex-wrap:wrap;font-size:12px;color:#52615c;margin-top:8px}.swatch{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:4px;vertical-align:-2px}.road-a-key{background:#b84e37}.road-b-key{background:#6b52a3}.road-u-key{background:#d1942d}.route-key{background:#325fd0}
@media(max-width:900px){.layout{grid-template-columns:1fr}.map-shell{min-height:500px}svg{height:68vh;min-height:500px}.detail{max-height:none}}
</style>
</head>
<body>
<header>
  <h1>Bath candidate neighbourhoods <span class="tag">THROWAWAY REVIEW</span></h1>
  <p>Closed planar areas formed from Official A, B and Classified Unnumbered lines. The sourced Bath extent filters the view; each retained candidate remains whole. A closed ring is candidate evidence only.</p>
  <div class="counts" id="counts"></div>
</header>
<div class="layout">
  <aside class="side">
    <h2>Layers</h2>
    <div class="controls">
      <label class="toggle"><input type="checkbox" id="show-polygons" checked> Candidate polygons</label>
      <label class="toggle"><input type="checkbox" id="show-roads" checked> Classified road lines</label>
      <label class="toggle"><input type="checkbox" id="show-entries" checked> Retained entry approaches</label>
    </div>
    <div class="buttons"><button id="fit-bath">Bath extent</button><button id="fit-all">All candidates and entries</button></div>
    <h2>Retained Bath entries</h2>
    <div class="entry-list" id="entry-list"></div>
    <p class="warning" id="onward-warning"></p>
    <div class="key">
      <span><i class="swatch road-a-key"></i>A Road</span>
      <span><i class="swatch road-b-key"></i>B Road</span>
      <span><i class="swatch road-u-key"></i>Classified Unnumbered</span>
      <span><i class="swatch route-key"></i>Retained approach</span>
    </div>
    <section class="detail" id="details" aria-live="polite"><h3>Inspect a feature</h3><p>Click a polygon, official road line, retained approach or entry marker.</p></section>
  </aside>
  <main class="map-shell">
    <div class="map-top"><span>Bath place extent: relation <b>5342409</b></span><span>Drag to pan · scroll to zoom</span></div>
    <svg id="map" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Interactive Bath classified-road candidate map"></svg>
  </main>
</div>
<script id="scene" type="application/json">{{SCENE}}</script>
<script>
const data=JSON.parse(document.getElementById('scene').textContent);
const svg=document.getElementById('map');
const ns='http://www.w3.org/2000/svg';
const initial=data.bathView;
let view={x:initial[0],y:initial[1],w:initial[2]-initial[0],h:initial[3]-initial[1]};
const palette=['#9ac6a7','#b1c9e3','#e7c893','#d6b3ce','#aad1cb','#d9c9a1','#b5c4a2','#c8b4a2'];
const classColor={'A Road':'road-a','B Road':'road-b','Classified Unnumbered':'road-unnumbered'};
function el(name,attrs={},parent=svg){const node=document.createElementNS(ns,name);for(const [k,v] of Object.entries(attrs))node.setAttribute(k,String(v));parent.appendChild(node);return node}
function esc(value){return String(value??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function setView(box){view={x:box[0],y:box[1],w:box[2]-box[0],h:box[3]-box[1]};drawView()}
function drawView(){svg.setAttribute('viewBox',`${view.x} ${view.y} ${view.w} ${view.h}`)}
function addTitle(node,text){const title=el('title',{},node);title.textContent=text}
function details(title,body){const area=document.getElementById('details');area.innerHTML=`<h3>${esc(title)}</h3>${body}`}
function intersectionList(entry){
 if(!entry.intersections.length)return '<p>No candidate polygon intersection on this retained approach.</p>';
 return `<p>Ordered intersections along the recorded approach:</p><ol>${entry.intersections.map(hit=>`<li><b>${esc(hit.candidate_id)}</b> at ${ (hit.start_m/1000).toFixed(2)} km; ${hit.length_m.toFixed(1)} m of line geometry in candidate.</li>`).join('')}</ol>`
}
function entryDetails(entry){
 const edgeList=entry.path_edge_ids.map(id=>`<li>${esc(id)}</li>`).join('');
 details(`${entry.name} → Bath urban entry`,
  `<p>Retained access geometry: ${(entry.path_length_m/1000).toFixed(3)} km. The path ends at the recorded crossing point on Bath extent relation ${esc(entry.extent_source_id)}.</p>`+
  `<p>Crossing edge: <code>${esc(entry.crossing_edge_id)}</code><br>Recorded fraction: ${Number(entry.crossing_fraction).toFixed(6)}</p>`+
  intersectionList(entry)+`<details><summary>Retained path edge IDs (${entry.path_edge_ids.length})</summary><ul>${edgeList}</ul></details>`+
  `<p class="muted">Source: corrected urban-entry planning.json. This path ends at the entry crossing; it is not an onward Bath route.</p>`);
}
function roadDetails(road){
 details(road.classification,
  `<p><b>${esc(road.road_name||road.road_number||'Unnamed official road')}</b>${road.road_number?` (${esc(road.road_number)})`:''}</p>`+
  `<p>Function: ${esc(road.road_function)}<br>Official feature ID: <code>${esc(road.id)}</code></p>`+
  `<p>Source: ${esc(road.source_id)} · effective ${esc(road.effective_date)} · ${esc(road.licence)}</p>`);
}
function polygonDetails(polygon){
 const sources=polygon.sources.map(row=>`<li>${esc(row.classification)} · ${esc(row.road_name||row.road_number||'Unnamed')} · <code>${esc(row.official_feature_id)}</code></li>`).join('');
 const classes=Object.entries(polygon.class_counts).map(([k,v])=>`${esc(k)}: ${v}`).join(' · ')||'No boundary source match';
 details(polygon.id,
  `<p><b>${polygon.area_ha.toFixed(4)} ha</b> measured in British National Grid (EPSG:27700)</p>`+
  `<p>Complete official-road polygon; intersects the Bath extent and is shown without clipping. Boundary records: ${polygon.source_count}.</p>`+
  `<p>${classes}</p><details><summary>Boundary official feature IDs</summary><ul>${sources}</ul></details>`+
  `<p class="muted">Planar closure is a candidate only. It does not establish an existing low-traffic neighbourhood, safe crossing, access permission or cycle provision.</p>`);
}
function addPath(group,d,attrs,click,title){const path=el('path',{d,...attrs},group);addTitle(path,title);if(click)path.addEventListener('click',ev=>{ev.stopPropagation();click()});return path}
const gPolygons=el('g',{id:'polygons'});
data.polygons.forEach((polygon,i)=>{
 const path=addPath(gPolygons,polygon.path,{class:'candidate','fill':palette[i%palette.length],'fill-rule':'evenodd','data-id':polygon.id},()=>polygonDetails(polygon),`${polygon.id} · ${polygon.area_ha.toFixed(4)} ha`);
 path.dataset.feature='candidate';
});
const gBath=el('g',{id:'bath-extent'});
addPath(gBath,data.bath.path,{class:'bath-boundary','fill-rule':'evenodd'},null,'Sourced Bath place extent, relation 5342409');
const gRoads=el('g',{id:'roads'});
data.roads.forEach(road=>{
 const path=addPath(gRoads,road.path,{class:`road ${classColor[road.classification]||''}`},()=>roadDetails(road),`${road.classification} · ${road.road_name||road.road_number||road.id}`);
 path.dataset.feature='road';
});
const gApproaches=el('g',{id:'approaches'});
const gMarkers=el('g',{id:'markers'});
data.entries.forEach((entry,index)=>{
 const color=palette[(index+2)%palette.length];
 const path=addPath(gApproaches,entry.path,{class:'approach','stroke':color},()=>entryDetails(entry),`${entry.name} retained approach to Bath · ${(entry.path_length_m/1000).toFixed(3)} km`);
 path.dataset.feature='approach';
 const start=el('circle',{cx:entry.start[0],cy:entry.start[1],r:5,class:'entry-start'},gMarkers);
 addTitle(start,`${entry.name} retained rural origin`);
 start.addEventListener('click',ev=>{ev.stopPropagation();entryDetails(entry)});
 const point=el('circle',{cx:entry.crossing[0],cy:entry.crossing[1],r:7,class:'entry-point'},gMarkers);
 addTitle(point,`${entry.name} recorded Bath entry crossing`);
 point.addEventListener('click',ev=>{ev.stopPropagation();entryDetails(entry)});
});
document.getElementById('counts').innerHTML=[
 ['Official boundary lines used',data.counts.official_lines_used],
 ['Complete closures',data.counts.full_polygon_count],
 ['Bath candidates shown',data.counts.bath_candidates],
 ['Local classified lines',data.counts.local_classified_lines],
 ['Retained Bath entries',data.counts.retained_bath_entries],
 ['Approach intersections',data.counts.entry_path_polygon_intersections]
].map(([label,value])=>`<span class="count"><strong>${value}</strong>${label}</span>`).join('');
document.getElementById('onward-warning').textContent=data.onward_route_status;
const entryList=document.getElementById('entry-list');
data.entries.forEach(entry=>{
 const button=document.createElement('button');button.className='entry-card';
 button.innerHTML=`<strong>${esc(entry.name)}</strong><small>${(entry.path_length_m/1000).toFixed(3)} km to Bath extent · ${entry.intersections.length} candidate intersections</small>`;
 button.addEventListener('click',()=>{entryDetails(entry);const b=entry.bounds;setView(b)});
 entryList.appendChild(button);
});
function groupToggle(id,group){document.getElementById(id).addEventListener('change',event=>{group.style.display=event.target.checked?'':'none'})}
groupToggle('show-polygons',gPolygons);groupToggle('show-roads',gRoads);groupToggle('show-entries',gApproaches);groupToggle('show-entries',gMarkers);
document.getElementById('fit-bath').addEventListener('click',()=>setView(data.bathView));
document.getElementById('fit-all').addEventListener('click',()=>setView(data.allView));
// Native SVG viewBox navigation keeps the file self-contained and needs no map server.
let drag=null;
svg.addEventListener('pointerdown',event=>{if(event.button!==0)return;drag={pointerId:event.pointerId,x:event.clientX,y:event.clientY,moved:false,view:{...view}}});
svg.addEventListener('pointermove',event=>{if(!drag||event.pointerId!==drag.pointerId)return;if(!drag.moved){if(event.clientX===drag.x&&event.clientY===drag.y)return;drag.moved=true;svg.setPointerCapture(event.pointerId);svg.classList.add('dragging')}const r=svg.getBoundingClientRect();const dx=(event.clientX-drag.x)/r.width*drag.view.w;const dy=(event.clientY-drag.y)/r.height*drag.view.h;view={x:drag.view.x-dx,y:drag.view.y-dy,w:drag.view.w,h:drag.view.h};drawView()});
svg.addEventListener('pointerup',event=>{if(drag&&event.pointerId===drag.pointerId){drag=null;svg.classList.remove('dragging')}});
svg.addEventListener('pointercancel',event=>{if(drag&&event.pointerId===drag.pointerId){drag=null;svg.classList.remove('dragging')}});
svg.addEventListener('wheel',event=>{event.preventDefault();const r=svg.getBoundingClientRect();const fx=(event.clientX-r.left)/r.width,fy=(event.clientY-r.top)/r.height;const scale=event.deltaY<0?.82:1.22;const w=view.w*scale,h=view.h*scale;view={x:view.x+(view.w-w)*fx,y:view.y+(view.h-h)*fy,w,h};drawView()},{passive:false});
drawView();
</script>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-root",
        type=pathlib.Path,
        default=REPO,
        help="checkout containing the pinned snapshot and corrected urban-entry artifacts",
    )
    args = parser.parse_args()
    started = time.perf_counter()
    scene, counts = make_scene(args.source_root.resolve())
    payload = json.dumps(scene, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
    document = HTML.replace("{{SCENE}}", payload)
    OUTPUT.write_text(document, encoding="utf-8")
    counts["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    counts["output_bytes"] = OUTPUT.stat().st_size
    print(json.dumps({"output": str(OUTPUT), **counts}, indent=2))


if __name__ == "__main__":
    main()
