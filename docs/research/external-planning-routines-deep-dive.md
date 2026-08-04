# Maintained graph-accessibility and route-challenge routines

- Date: 4 August 2026
- Status: research note for issue 313; no implementation or ADR
- Scope: maintained, locally reproducible routines for graph diagnostics,
  reachability/accessibility, route challenges and observation-to-asset matching.
- Evidence rule: the links below are first-party documentation or source trees.
  An external result is an immutable **Governed External Analysis Run**, never a
  live compiler dependency, foreign geometry authority or hidden policy score.

## Decision summary

Keep SATN's deterministic topology, canonical geometry, officer challenge and
publication rules in the core. Use an external engine only in a separately pinned
run whose input Source Exports, profile, engine version, licence, configuration,
seed/thread policy, output schema and hashes are retained. External output may be
admitted as claim-specific Evidence Observations; it cannot repair SATN geometry,
establish legal access, select a route or replace an explicit unknown.

The most useful first candidates are OSMnx/NetworkX for descriptive graph
diagnostics, r5r/R5 for multimodal accessibility comparisons, and Valhalla or FMM
for offline observation-to-road matching. OTP is valuable for a transit/wheelchair
scenario but operationally heavier and its old REST/analytics surfaces are not a
stable core interface. `anime` is relevant to linework-to-linework enrichment but
is young and under-documented; keep it experimental. OSRM's match service is a
credible self-hosted comparator, not a hosted endpoint dependency.

## Candidate records

### OSMnx 2.1.1 + NetworkX (graph diagnostics)

- **Symbols/API and algorithm.** [`osmnx.stats.basic_stats`](https://osmnx.readthedocs.io/en/stable/user-reference.html#osmnx.stats.basic_stats) returns counts, degree, lengths, density, self-loop, intersection, street-segment and average circuity measures. `area=None` suppresses density; `clean_int_tol=None` suppresses cleaned-intersection measures. [`circuity_avg(Gu)`](https://osmnx.readthedocs.io/en/stable/user-reference.html#osmnx.stats.circuity_avg) sums edge length / endpoint straight-line distance (Euclidean for projected CRS, great-circle otherwise; `None` if total length is zero). [`count_streets_per_node`](https://osmnx.readthedocs.io/en/stable/user-reference.html#osmnx.stats.count_streets_per_node) uses an undirected graph and self-loop handling to count physical streets. [`largest_component(G, strongly=False)`](https://osmnx.readthedocs.io/en/stable/user-reference.html#osmnx.truncate.largest_component) returns the largest weak (default) or strong component. [`shortest_path(G, orig, dest, weight='length', cpus=1)`](https://osmnx.readthedocs.io/en/stable/user-reference.html#osmnx.routing.shortest_path) and `k_shortest_paths(..., k, weight='length')` (Yen) return node paths; `route_to_gdf` resolves parallel edges by the weight. OSMnx delegates component sets, degree/dangle derivation and reachability to [NetworkX's graph algorithms](https://networkx.org/documentation/stable/reference/algorithms/).
- **Inputs/outputs and assumptions.** A `MultiDiGraph` with node coordinates, edge geometry/length and a declared CRS; output is a dict, scalar, component graph or ordered edge `GeoDataFrame`. `basic_stats` describes the supplied graph only and does not infer legal access, split crossings or barriers. A degree-one/dangle report is a SATN profile expression over the directed/undirected representation, not an OSMnx “quality” result. Network distance requires non-negative, comparable edge weights; missing/disconnected pairs yield no path/exception depending on the delegated NetworkX call.
- **Determinism, failure and burden.** Given pinned graph bytes, CRS, weight, component mode and stable node/edge ordering, calculations are deterministic; `cpus>1` and parallel shortest-path scheduling require output sorting before hashing. OSMnx is MIT-licensed ([official licence](https://github.com/gboeing/osmnx/blob/main/LICENSE)) and actively documented, but it follows Python/GEOS/NetworkX dependency versions; pin the full environment. SATN already has dominant-component attachment, branch/meeting tree, route distance and elevation challenge logic. The gap is a reusable, profile-bound diagnostic record for dangles, components, circuity/directness, severance and reachability—not a replacement for those semantics.
- **Placement and officer benefit.** Core-adjacent external analysis: run offline against the immutable SATN snapshot to expose topology and candidate consequences; retain metrics as evidence, never as automatic selection. It gives officers an explainable “what disconnects if this barrier/edge is removed?” table and directness/circuity challenge evidence.
- **Synthetic proving fixture.** Projected graph: A–B (100 m), B–C (100 m), B–D (100 m), plus isolated E–F (50 m); directed reverse edges for bidirectional links. Assert `n=6`, weak components sizes 4 and 2, `streets_per_node` B=3, one degree-one dangle C and D (after excluding isolated component by profile), `edge_length_total=700 m` for the directed representation and `street_length_total=350 m` for its undirected physical-street representation, `circuity_avg=1` for straight segments, and no A→E path. Remove B–D and assert D becomes a dangle and reachable set from A loses D; hash sorted IDs and metric values.

### R5 via r5r 2.4.0 (multimodal accessibility)

- **Symbols/API and algorithm.** [`accessibility()`](https://ipeagit.github.io/r5r/reference/accessibility.html) consumes a `build_network()` R5 network, WGS84 origin/destination points and one or more opportunity columns. It computes one-to-many travel-time matrices over a departure `time_window` (default 10 minutes), then applies `step` (cumulative opportunities, default), `logistic`, `fixed_exponential`, `exponential` or `linear` decay. `percentiles` (default 50; max five) selects travel-time percentile, not an accessibility-distribution percentile. `travel_time_matrix`, `expanded_travel_time_matrix`, `detailed_itineraries`, `isochrone`, `find_snap` and `street_network_to_sf` are documented in the [r5r function index](https://ipeagit.github.io/r5r/reference/).
- **Parameters/defaults and outputs.** Defaults include `mode='WALK'`, `mode_egress='WALK'`, `max_trip_duration=120` minutes, `walk_speed=3.6 km/h`, `bike_speed=12 km/h`, `max_rides=3`, `max_lts=2`, `draws_per_minute=5`, `n_threads=Inf`, `time_window=10`, `decay_function='step'`, `output_dir=NULL`. `max_walk_time`, `max_bike_time` and `max_car_time` default to `Inf`; `new_carspeeds` and `new_lts` are explicit edge/polygon patches. Output is a `data.table` by origin/opportunity/percentile/cutoff, or per-origin CSVs when `output_dir` is set. Frequency-based GTFS uses Monte Carlo draws; no-frequency feeds are deterministic across a time window.
- **Assumptions/failure modes.** Inputs need WGS84 point IDs (`id`, `lon`, `lat`), OSM PBF, optional GTFS and DEM; departure time affects transit and must be valid for GTFS calendars. LTS is an OSM-derived stress model, not an officer safety finding. Snap failures, missing opportunities, invalid CRS/calendar and unreachable pairs must be retained as explicit missing/unknown. `r5r` requires JDK 21 and runs a downloaded R5 JAR; R5 itself states that it has no stable public API and third-party deployments may break across releases ([R5 README](https://github.com/conveyal/r5#readme)). R5 is MIT; r5r offers GPL-3.0-or-later or MIT ([r5r licence](https://github.com/ipeaGIT/r5r/blob/master/LICENSE)). The Java/R runtime and GTFS/OSM/DEM preprocessing remain a material operational burden.
- **Determinism, SATN gap and placement.** Pin R5/r5r versions, JDK, PBF/GTFS/DEM bytes, departure timezone, profile, thread count, seed and draw count; sort output rows before hashing. SATN has no transit accessibility or opportunity-decay routine. Admit an accessibility comparison as governed external evidence only; never treat it as predicted demand, legal access, route quality or canonical geometry. Officer benefit is a transparent “opportunities reachable under scenario X versus baseline Y” table and sensitivity across cutoffs/modes.
- **Synthetic proving fixture.** Four WGS84 points on a 1-km walkable square: O1 has 10 jobs at 5 min, O2 has 20 at 15 min; a second disconnected destination has 100 jobs. With `mode='WALK'`, `walk_speed=3.6`, `time_window=0`, `decay='step'`, `cutoffs=10`, assert O1=10, O2=0 (or the profile-calculated travel times), disconnected=0/NA with a retained unreachable code. Re-run with cutoff 20 and assert O1=30; compare two pinned scenarios and assert only the changed edge/opportunity contributes to the delta.

### OpenTripPlanner 2.9.0 (transit, wheelchair and travel-time surface)

- **Version/API.** The maintained repository lists [v2.9.0 (18 March 2026)](https://github.com/opentripplanner/OpenTripPlanner/releases/tag/v2.9.0). OTP2 exposes GTFS and Transmodel GraphQL APIs, vector tiles and actuator/health APIs; the [official API overview](https://docs.opentripplanner.org/en/latest/apis/Apis/) says the REST API was removed in 2025. The former sandbox [Travel Time (Isochrone & Surface) API](https://docs.opentripplanner.org/en/v2.5.0/sandbox/TravelTime/) accepts `location`, ISO-8601 `time`, one or more `cutoff`, `modes` and `arriveBy`, returning GeoJSON contours or a one-band GeoTIFF; because it is versioned sandbox documentation, verify it exists in the pinned OTP build before use.
- **Route challenge contract.** [`RouteRequest`](https://docs.opentripplanner.org/en/v2.6.0/RouteRequest/) exposes `wheelchairAccessibility.enabled=false`, `inaccessibleStreetReluctance=25`, `maxSlope=0.083`, `slopeExceededReluctance=1`, `stairsReluctance=100`; stop/trip/elevator `onlyConsiderAccessible`, `unknownCost` and `inaccessibleCost` defaults are explicit. `maxDirectStreetDuration` defaults to 4 h as a performance bound, and near-limit results are not guaranteed optimal. These are routing costs/constraints, not SATN policy or accessibility certification.
- **Inputs/outputs and assumptions.** Build from OSM plus GTFS/NeTEx and a pinned router config; request points, modes, date/time, arrive/depart and wheelchair profile. GraphQL returns itineraries/legs and sandbox travel-time returns contours/surface. Transit calendar/service period, timezone, stop accessibility metadata and elevation are authoritative assumptions; missing/unknown accessibility is costed unless `onlyConsiderAccessible=true`. A server/JVM graph build, feed updates and config compatibility are the operational burden. OTP is LGPL-3.0-only; retain licence notices and exact JAR/config.
- **SATN equivalent/gap, placement and officer benefit.** SATN's elevation challenge, explicit unknowns and route-selection assessments are equivalent in governance but not in temporal transit routing. Keep OTP entirely in Governed External Analysis: it can provide a bounded “wheelchair profile versus ordinary profile” comparison or transit catchment challenge, never alter SATN geometry or mark a route accessible. This is useful where an officer needs first/last-mile or barrier alternatives and service-time sensitivity.
- **Synthetic proving fixture.** Build a three-stop GTFS line and two OSM street paths: direct path has a 12% slope and stairs; detour is 500 m flat. At the same departure time, assert ordinary WALK chooses the shorter path, wheelchair profile with `maxSlope=0.083` and `stairsReluctance=100` chooses/penalises the detour, and an absent elevator/stop accessibility flag remains “unknown” rather than “accessible”. Hash GraphQL itinerary IDs, legs, cost profile and request.

### Valhalla 3.8.2 (matrix, isochrone, route challenge and matching)

- **Version/licence/source.** [Release 3.8.2](https://github.com/valhalla/valhalla/releases/tag/3.8.2), commit `17af0d0` (8 July 2026), MIT-licensed. Primary API references: [matrix](https://valhalla.github.io/valhalla/api/matrix/), [isochrone](https://valhalla.github.io/valhalla/api/isochrone/) and [map matching](https://valhalla.github.io/valhalla/api/map-matching/).
- **Symbols/algorithm and contract.** `/sources_to_targets` accepts ordered source/target `{lat,lon}` arrays, `costing` (`auto`, `bicycle`, `pedestrian`, etc.), optional per-location `date_time`, `matrix_locations`, `expansion_max_distance`, `verbose` (default `true`) and `shape_format`; output is row-major `time`/`distance` (or compact durations/distances), null for unfound pairs, and an algorithm label (`timedistancematrix`, `costmatrix`, or `timedistancebssmatrix`). Time-dependent matrices have source/target count restrictions and different exact/faster algorithms. `/isochrone` accepts a location, costing, up to four `contours` (minutes or km), `polygons=false`, `denoise=1`, `generalize` and `reverse`; output is GeoJSON contours or GeoTIFF grid. `trace_route` returns a snapped route; `trace_attributes` returns matched edges, OSM way IDs, edge/node attributes and `matched_points`.
- **Matching details, failure and assumptions.** `shape_match` is `edge_walk`, `map_snap` (more expensive) or default `walk_or_snap`; `trace_options` defaults are not silently assumed—record `search_radius`, `gps_accuracy`, `breakage_distance`, `interpolation_distance`, timestamps/durations and costing. Discontinuities split traces; attributes are Valhalla-normalized routing values, not raw OSM tags. Isochrone `denoise`/Douglas–Peucker `generalize` can drop contours or self-intersect. Self-hosting requires tile building, elevation/traffic data and a daemon; never call a public endpoint in compilation.
- **SATN gap/placement and officer benefit.** SATN has bounded bidirectional cycling routing, detour/elevation challenges and explicit gaps, but no one-to-many matrix, isochrone or GPS-to-edge matcher. A pinned local Valhalla run can expose barrier severance, reachable catchments and observed-edge attributes for officer review; output remains an external observation and cannot become foreign geometry authority.
- **Synthetic proving fixture.** Four nodes in a square with pedestrian edges 100 m each and one disconnected node. Matrix `sources=[A]`, `targets=[A,B,C,D,E]`, `costing='pedestrian'` must return 0,100,200,100 and null for E; `matrix_locations=2` may return only the two closest and must be recorded. Isochrone 2-minute contour includes A–D but not E. A trace with points on A–B–C and `shape_match='map_snap'` must return two matched edges, monotone `distance_along_edge`, OSM way IDs and zero discontinuity; a 1-km jump must produce a discontinuity/warning, not a fabricated bridge.

### FMM (Fast Map Matching) framework, 2020.01.31 / current master

- **Source/API and algorithm.** The maintained [FMM repository](https://github.com/cyang-kth/fmm) is Apache-2.0 and provides C++/Python/CLI FMM and STMatch algorithms. It uses an R-tree for candidates, precomputed UBODT for fast routing (FMM) or no precomputation (STMatch), OpenMP parallelism, and returns traversed path, geometry, matched edges, GPS error and offsets. CLI defaults documented in the source are `candidates=8`, `radius=300 m`, `error=50 m`, `pf=0`; network/GPS IDs and output fields are configurable.
- **Inputs/outputs, assumptions and failures.** Input is a correctly noded directed/undirected network (OSM/Shapefile/GeoPackage) and point/trajectory CSV or Shapefile; raw OSM direct input was removed because topology errors are unsafe. UBODT must match the exact network and threshold; candidate radius/GPS-error and sparse/noisy observations can yield unmatched points or wrong paths. C++/GDAL/Boost/CMake/OpenMP plus Python bindings are a material build burden. The repository reports version `2020.01.31` in its CLI; pin a commit/tag rather than floating master.
- **SATN equivalent/placement and benefit.** SATN currently snaps governed points and records source IDs but does not infer an observed traversal path. FMM is a Governed External Analysis candidate for matching dated GPS/field traces to existing SATN edges; it must not create or alter assets, infer legal access, or override an officer observation. It can provide reproducible edge-coverage and observed-vs-asset evidence for route challenge.
- **Synthetic proving fixture.** Network A–B–C (100 m each), parallel A–D–C (120 m each), observations at A, 105 m along A–B, C with ±5 m noise. Pin `radius=50`, `error=10`, candidates=8 and exact UBODT. Assert all observations map to A–B–C, edge sequence is contiguous, total matched path is 200 m ± tolerance, and a point 500 m away is explicitly unmatched. Re-run with same inputs and assert identical edge IDs and offsets.

### OSRM 26.7.3 (self-hosted match/nearest comparator)

- **Contract.** [Release v26.7.3](https://github.com/Project-OSRM/osrm-backend/releases/tag/v26.7.3), commit `0844e3a`, BSD-2-Clause. The official [HTTP API](https://project-osrm.org/docs/v26.6.1/http) documents `/match/v1/{profile}/{coordinates}` (HMM-style plausible trace, `steps`, `geometries`, `overview`, `annotations`) and `/nearest` for snapping. Large timestamp gaps or improbable transitions split sub-traces; unmatched outliers may be removed. The backend supports C++/Node/Python bindings, but prepared CH/MLD data and a local `osrm-routed` process are required.
- **SATN use and limits.** Useful as an independent comparator for FMM/Valhalla matching or route distance, not as a hosted service and not as canonical geometry. Profiles are mode-specific and static; OSM extraction/profile decisions, split traces and outlier removal must be retained. Synthetic fixture: same A–B–C trace as FMM; assert contiguous sub-trace and identical candidate edge set under a pinned profile, then assert a >60 s jump creates separate sub-traces. Any disagreement is a review finding, not an automatic winner.

### `anime` Rust 0.1.2 (linework-to-linework enrichment; experimental)

- **API/algorithm.** [`anime` 0.1.2](https://docs.rs/anime/0.1.2) (20 December 2024) exposes `Anime`, `MatchCandidate`, `AnimeError`, `MatchesMap`, `SourceTree` and `TargetTree`. Its documented algorithm builds two R*-trees over component lines, expands target AABBs by distance threshold `DT`, filters candidate pairs by slope-angle threshold `AT`, domain/range overlap and geometric distance, then stores partial source↔target overlap lengths in a `BTreeMap` for enrichment. Inputs are `Vec<geo::LineString>` for source/target; output is match candidates/lengths, not a routable graph.
- **Operational/maintenance assessment.** Dependencies are `geo ^0.27`, `geo-types ^0.7.12` and `rstar ^0.11`; docs coverage is only 54.84% with no examples, and the latest listed release is 0.1.2. Treat as experimental despite a permissive Rust ecosystem and inspect the crate's licence metadata before any adoption. No live service is needed, but Rust FFI/CRS/geometry conversion and sparse tests raise integration burden.
- **SATN placement and benefit.** This is the only “anime” found that is semantically relevant (Approximate Network Matching, Integration, and Enrichment), not animation software. Keep it outside core as a candidate for matching observed or proposed linework to existing SATN asset sections. It cannot decide equivalence, continuity, legality or intervention need; all thresholds and unmatched portions must be visible.
- **Synthetic proving fixture.** Source lines A=(0,0)–(10,0) and B=(10,0)–(20,0); target lines A'=(0,0.5)–(10,0.5), C=(30,0)–(40,0). With `DT=1` and a small angle threshold, assert A↔A' candidate with overlap length 10, C unmatched, and reversing A' orientation does not silently become a different asset without an explicit orientation policy. Hash sorted candidate IDs, overlap lengths and thresholds.

## Cross-cutting governing profile and bounded fixture

Every run should record: source-export IDs/hashes and CRS; network/asset IDs;
engine version/commit, licence and environment; profile parameters/defaults;
timezone/date and OSM/GTFS/DEM/elevation versions; thread count and random seed;
input/output schemas; warnings, null/unreachable/unmatched records; and canonical
sorted result hash. A profile must declare whether it measures directed or
undirected connectivity, dangle definition, directness denominator, barrier
semantics, accessibility cutoff/decay and acceptable geometry tolerance.

The shared proving fixture is deliberately tiny: a four-node square, one isolated
node, one parallel detour, one 12%/stairs edge, two opportunity points and a
three-point noisy trace. Assertions above must hold independently for each engine.
The fixture proves contract and determinism only; it does not validate OSM
completeness, legal access, safety, feasibility or policy weights.

## Rejected or unresolved claims

- No maintained primary source was found for a transport “ANIME” package other than
  the Rust `anime` crate described above; animation/vision AnimeInbet work is out of
  scope.
- OSMnx/NetworkX, R5, OTP, Valhalla, FMM, OSRM and `anime` do not supply SATN's
  officer governance, legal-access authority, evidence lineage or canonical geometry.
- Public routing endpoints, hosted R5/OTP/Valhalla/OSRM services, unpinned OSM
  extracts and hidden profile defaults are expressly rejected. They violate
  disconnected reproducibility and would make a route or accessibility result an
  opaque policy authority.
