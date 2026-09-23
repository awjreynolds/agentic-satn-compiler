# LTN candidate tools: boundaries, permeability, and reusable design components

Research for [issue #580](https://github.com/awjreynolds/agentic-satn-compiler/issues/580), under map579.

- Retrieved: 2026-09-23
- Branch: `codex/research-ltn-tools`, based on `main` at `7ba15a1`
- Source snapshots inspected: CycleStreets `02bdb18`, A/B Street `9f461b8`
- Scope: candidate neighbourhoods bounded by classified roads, with network connectivity evidence
- Excluded: demographic prioritisation, a ranking score, filter policy, safety/provision claims, and an automatic 1 km² gate

## Decision in brief

[A/B Street LTN v2](https://github.com/a-b-street/ltn) is the closest reusable design reference. It can generate candidate areas from OSM severances, let a user draw or merge a boundary, place motor-traffic filters, split the interior into driving-connectivity cells, and inspect changed driving routes. Its backend is Rust compiled to WebAssembly and its project is Apache-2.0 licensed. Its boundary classes and route assumptions are OSM proxies, however, and its Rust/WASM functions are an internal application API rather than a stable library contract.

[CycleStreets' LTN map](https://www.lowtrafficneighbourhoods.org/) is useful evidence for existing modal-filter patterns and physically possible car through-streets. It is a server-backed mapping site: the public frontend requests precomputed API layers and does not let a planner draw a candidate or simulate a proposed filter set. The site says its through-street analysis uses C roads and above as dividing roads, but the analysis is based on OpenStreetMap and reports about 80–90% beta accuracy, so it is not an authority classification or a traffic count.

The 2025 Newcastle study, [Identification of plausible low traffic neighbourhoods using open data](https://eprints.ncl.ac.uk/308799), is the closest batch-method reference. It separates urban areas at active-mode severances and measures modal-filter density, vehicle through-routes, and active-mode versus vehicle permeability. It is a CC BY 4.0 paper, but the public record only points to a GitHub page in the manuscript; no standalone API or Rust component was established in this bounded check.

For SATN, implement the smallest local Rust pass over the existing classification and source fields. Identify areas enclosed by classified-road edges, emit the candidate polygon, and check whether the existing active-travel graph connects its interior. Keep incomplete boundary or connection evidence as unknown while still showing the candidate; treat “around 1 km²” as context, never as a subdivision or acceptance threshold. Leave filter design, subdivision, demographic ranking, and motor-traffic analysis for later unless existing data makes the latter necessary. Use A/B Street’s cell ideas as a review reference or optional external comparison, rather than embedding either application.

## Comparison

| Tool | Boundary derivation and size | Classification and connectivity | Proposal, export, and reuse | Fit for SATN |
| --- | --- | --- | --- | --- |
| CycleStreets LTN map | No user-drawn candidate boundary in the public site or frontend repository. The analysis divides local streets by identified main roads and publishes authority-area statistics; it does not target 1 km² shapes. | OSM street-pattern analysis detects modal filters. Separate car-routing tests mark whether local streets can connect two main roads; the site explicitly says this means physically possible through-traffic, not observed rat-running. Dividers are described as C roads and above, with exceptions. | JS/HTML/CSS frontend; API layers include modal filters, LTN streets, and statistics, with GIS download links in the layer properties. GPL-3.0-only; no repository releases. No local filter editor or documented reusable library. | Reference data and QA lead, not the candidate generator. Useful for checking OSM-based filter/through-route hypotheses, never a substitute for authority road classification. |
| A/B Street LTN v2 / Connected Neighbourhood Tool | Automatic boundaries split settlements using OSM “severances” (motorway through tertiary highway tags, plus railways/waterways and settlement context). The UI also draws a boundary by snapping points to roads, permits unsnapped points, and merges adjacent generated areas. It reports `area_km2`; automatic generation has implementation filters of 0.0025–50 km², not a 1 km² design rule. | `Road::is_severance` is an OSM `highway=*` proxy and can be manually reclassified in the app. Interior cells are flood-filled for driving while respecting modal/diagonal filters and turn/travel-flow restrictions. The route tool is a car graph search using OSM restrictions and speed-limit duration costs, with an optional main-road slowdown. | Imports custom areas from Overpass or OSM XML/PBF and saves project state as GeoJSON. Supports proposed walk/cycle-only barriers, bus gates, school streets, diagonal filters, one/two-way edits, crossings, shortcuts, cells, and route comparisons. Rust backend → WASM, Svelte frontend, Apache-2.0; `backend/src/lib.rs` exposes type-unsafe app bindings, not a stable library API. | Closest interaction model. Reuse the boundary and connectivity ideas against SATN’s existing classification/source fields and active graph; do not add a new input schema just to mirror this tool. |
| Larkin, Robson & Ford (2025) | Batch method separates city areas where walking/cycling can proceed before a severance such as a major road. The abstract does not establish a fixed area target or a user-editable boundary workflow. | OSM metrics cover modal-filter density, vehicle through-routes, and active-mode/vehicle permeability difference; results are combined into a plausibility score. | CC BY 4.0 article and automatically generated web maps; the paper’s data statement points to code on a GitHub page named in the manuscript, but no stable API/library was located in this check. | Methodological reference for a later batch diagnostic, not a dependency for the focused boundary POC. Do not import its score or demographics into the current contract. |

## Source findings

### Boundary provenance

CycleStreets says it analyses OpenStreetMap street patterns to infer modal filters and treats C roads and above as the roads dividing areas. Its own technical note says the through-street work uses car-routing tests between identified main roads and heuristics to remove unlikely cases. The frontend config requests already-produced `advocacydata.modalfilters`, `advocacydata.ltns`, and `advocacydata.ltnstatistics` layers; it does not contain a boundary-drawing or proposed-filter engine. See the [public explanation](https://www.lowtrafficneighbourhoods.org/), [frontend layer configuration](https://github.com/cyclestreets/lowtrafficneighbourhoods.org/blob/main/src/lowtrafficneighbourhoods.js), and [OSM technical discussion](https://lists.openstreetmap.org/pipermail/talk-gb/2021-June/027185.html).

A/B Street v2 has both automatic and human boundary paths. The automatic implementation uses `Road::is_severance`, whose source list is OSM `motorway`, `trunk`, `primary`, `secondary`, `tertiary` and their links, then adds railways/waterways and settlement edges before polygon splitting. The user guide says areas can instead be drawn in detail, snapped to roads or partly freehand. The [automatic boundary implementation](https://github.com/a-b-street/ltn/blob/main/backend/src/auto_boundaries.rs), [road classification](https://github.com/a-b-street/ltn/blob/main/backend/src/map_model.rs), and [user guide](https://a-b-street.github.io/ltn/user_guide.html) make this explicit. The technical notes also acknowledge that OSM major-road classifications can be wrong and that custom boundaries are needed for cases where local knowledge differs ([technical details](https://a-b-street.github.io/docs/software/ltn/tech_details.html)).

The official Walk Wheel Cycle Trust design guide says an LTN boundary is formed by roads left open to through traffic and should follow roads suited to heavier traffic; it describes an indicative 1–1.5 km² range while also saying size depends on local context. TfL’s Strategic Neighbourhood Analysis similarly divides London with movement-class roads and says its area limits were for analysis robustness, not scheme suitability. These are useful policy context, not a SATN area gate: [definition guide](https://www.walkwheelcycletrust.org.uk/our-services/infrastructure-design-guidance/an-introductory-guide-to-low-traffic-neighbourhood-design/an-introductory-guide-to-low-traffic-neighbourhood-design-contents/3-low-traffic-neighbourhood-definition/) and [TfL SNA](https://content.tfl.gov.uk/lsp-app-six-b-strategic-neighbourhoods-analysis-v1.pdf).

Neither product establishes an official highway-authority classification from its public OSM workflow. SATN should use its existing authoritative road IDs/classifications and source fields when present. An OSM `highway` class can be shown as a proxy or cross-check, never silently promoted to authoritative classification.

### Area and shape

CycleStreets publishes statistics for its analysis areas, including street-length proportions and lengths, but the public tool does not provide a candidate polygon editor or a documented shape/area export contract. A/B Street computes polygon area and preserves a GeoJSON boundary, but its automatic generator can emit many different shapes and has geometry edge cases around water, rail, bridges, map edges, and imperfect road tracing. The A/B source and tests demonstrate that boundary drawing and road assignment are separate concerns. The initial SATN pass should show the candidate polygon from existing classified-road evidence, check existing active-graph connections, and preserve unknowns where either is incomplete; it should not add a new perimeter/holes/closure reporting framework or subdivide a candidate merely to approach 1 km².

### Through routes and modes

CycleStreets’ route test is car-specific and OSM-based. Its “through traffic possible” label is a physical-capability result; the site explicitly disclaims a measurement of traffic actually using the route. It does not simulate an alternative proposal.

A/B Street’s cell algorithm is also explicitly for motor-vehicle connectivity. Its modal filters block larger vehicles while active modes remain conceptually passable, and its route comparison shows changed driving paths before/after edits. The route cost is a speed-limit duration calculation, with a fixed UK unprotected-right-turn penalty and optional main-road slowdown documented in the [technical notes](https://a-b-street.github.io/docs/software/ltn/tech_details.html). That is useful for inspecting connectivity and shortcut exposure, but it is not a calibrated traffic forecast and should not be copied into SATN’s rural-access evidence. The focused SATN POC should first check connections in the existing active-travel graph and retain unknown permissions; motor-vehicle analysis can follow later unless required by existing data.

### Inputs, export, and licence

CycleStreets’ repository is a small GPL-3.0-only browser frontend (`yarn`, Mapbox layer viewer, `osmtogeojson`) around CycleStreets API endpoints; see the [README](https://github.com/cyclestreets/lowtrafficneighbourhoods.org/blob/main/README.md), [package metadata](https://github.com/cyclestreets/lowtrafficneighbourhoods.org/blob/main/package.json), and [license](https://github.com/cyclestreets/lowtrafficneighbourhoods.org/blob/main/LICENSE.md). Reusing its server analysis would require its API/data pipeline and key/configuration assumptions. There is no standalone Rust crate.

A/B Street v2 is an Apache-2.0 Rust/WASM application with npm and Cargo build steps. Custom imports use current Overpass data; deterministic built-in study areas are clipped OSM PBFs. Project state and test outputs are GeoJSON, which is a useful interchange format. The [developer README](https://github.com/a-b-street/ltn/blob/main/README.md) says the backend is compiled to WASM and that the frontend/backend interface is intentionally thin; treat internal modules as reference code, not as a versioned API. Verify third-party notices before any future code copy.

The 2025 paper is CC BY 4.0 ([Newcastle record](https://eprints.ncl.ac.uk/308799)); its OSM inputs and generated web maps are reproducible in principle, but the bounded check did not identify a maintained package or API endpoint.

## Smallest SATN reuse path

1. Reuse the existing SATN classification, source, geometry, and edge identifiers; keep OSM `highway` as a separate optional proxy field. Do not add a parallel input schema.
2. Walk classified-road connections to identify enclosed candidate areas and emit the candidate polygon. Do not split or rank by area; record the approximate 1 km² context only as a displayed measurement.
3. Check whether existing active-travel graph edges connect the candidate interior and preserve unknowns for missing or ambiguous permissions. Showing the polygon does not depend on completing that check.
4. Export the candidate through the existing GeoJSON/map path with its source fields and connection result. Do not add a filter editor or traffic simulator to this focused pass.
5. Use A/B Street manually or as a separately pinned comparison executable when a reviewer needs interactive filter exploration. Do not link SATN to its internal WASM API or to CycleStreets’ hosted endpoints for the focused POC.

## Uncertainty and rejected claims

- OSM road classes and CycleStreets’ C-road label are proxies; they do not establish the local authority’s legal/maintenance classification.
- CycleStreets’ beta analysis reports estimated 80–90% accuracy and says its through-route result is physical possibility, not observed rat-running.
- A/B Street’s automatic polygon generator and car route costs are useful design aids, not evidence of safety, adoption, provision, or future traffic volumes.
- The 2025 study is a promising batch methodology, but its reusable code/API location was not confirmed from the public record during this bounded search.
- No source reviewed imposes a universal 1 km² LTN rule. Area should remain descriptive and locally reviewable.
