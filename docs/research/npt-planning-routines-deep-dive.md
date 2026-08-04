# NPT planning routines: source-level deep dive (2026-08-04)

## Scope and decision

This is a source-level inventory of reusable routines from the official NPT Scotland
repositories and their maintained successors. It separates **data shape/method** from
Scottish source data and policy. The proposed SATN seams are provider-neutral; none of
these routines is safe to import with its defaults or policy labels unchanged.

Repository snapshots inspected: `npt@86662723f5b1cd26b0dca867ab36960a8c34de79`
(2025-05-19), `corenet@aaed15de5c3a4da9ed3b1b5d5220fbd1a373a02f` (2025-07-03),
`osmactive@9b3a2089fc2ae5082c99e5edcae277fad987cf6d` (2026-07-23),
`networkmerge@1004edd8104093a272d1588ecd69452496010f0b` (2025-09-22),
`parenx@ad5bf6f4c761865b46c174c45dc7de849abb2dc1` (2025-08-20), and
`rnetmatch@7eecbc10088b5972206a0c84a6ef9aef990717ef` (2024-12-21).

## Routine cards

### 1. Flow aggregation over route lines (`npt::make_rnets`, `combine_rnets`)

* **Method/data shape.** Inputs are routed `sf` line segments with bicycle-flow
  attributes; `make_rnets()` filters rows with any positive bicycle measure, calls
  `stplanr::overline2()` with `attrib = c("bicycle", "bicycle_go_dutch",
  "bicycle_ebike")`, `fun = sum`, `ncores = 1`, `regionalise = 1e5`, then rounds and
  suppresses positive values below 10 to 3. `combine_rnets()` prefixes route-purpose
  columns, fills numeric NAs with zero, calls `stplanr::overline()` with `sum` and
  `max`, removes non-cycling rows, and optionally derives `all_*` sums.
  [make_rnets](https://github.com/nptscot/npt/blob/86662723f5b1cd26b0dca867ab36960a8c34de79/R/rnet_functions.R#L9-L33),
  [combine_rnets](https://github.com/nptscot/npt/blob/86662723f5b1cd26b0dca867ab36960a8c34de79/R/rnet_functions.R#L44-L126)
* **Assumptions/failure.** `overline2()` semantics (splitting, overlap, CRS and
  ordering) are delegated; duplicate names, unnamed lists, all-NA attributes, and
  zero-flow filtering can silently drop evidence. Suppression is disclosure policy,
  not a transport-method invariant. Parallel execution/order is not specified.
* **SATN equivalent/gap and seam.** SATN has route/network aggregation but no
  provider-neutral extensive-vs-intensive contract. Add `aggregate_line_attributes`
  with explicit `source_id`, `target_id`, shared length, aggregation law, missing-value
  law, privacy transform, CRS and deterministic ordering. Keep raw flows and policy
  suppression separate. Officer benefit: reproducible demand totals without counting
  a long source segment multiple times.
* **Fixture.** Two parallel target lines and three source pieces with lengths 5, 10,
  5 and flow 10 each: assert extensive result is 20 on the target, intensive result is
  length-weighted mean, reversed geometry preserves totals, and a value 9.9 is not
  silently changed unless a declared privacy policy is enabled.

### 2. Spatial/angle join and weighted attributes (`corenet::anime_join`)

* **Method/data shape.** `anime_join()` calls `anime::anime(source,target,
  angle_tolerance=35, distance_tolerance=15)`, obtains `source_id/target_id` matches,
  multiplies the selected source attribute by each named weight (except `max`, which is
  unweighted), groups by target and applies `agg_fun` with `na.rm=TRUE`; `max` is then
  rounded. The NPT preparation call uses `attribute = all_fastest_bicycle_go_dutch`,
  `agg_fun=sum`, `weights=target_weighted`, 35 degrees/15 m.
  [wrapper](https://github.com/nptscot/corenet/blob/aaed15de5c3a4da9ed3b1b5d5220fbd1a373a02f/R/anime_join.R#L45-L133),
  [NPT call site](https://github.com/nptscot/corenet/blob/aaed15de5c3a4da9ed3b1b5d5220fbd1a373a02f/R/corenet.R#L91-L126)
* **Failure/unsafe defaults.** No CRS/units validation; angle is a line-bearing
  tolerance (not a policy of “parallel road”), and missing/zero weights plus duplicate
  IDs can bias or erase matches. `agg_fun=max` ignores weights by design. Match order
  and `row_number` IDs are positional, so input reorder changes results.
* **Seam/fixture.** SATN should expose a candidate matcher returning evidence rows
  (`source_id,target_id,shared_length,angle,distance`) and a separate aggregation
  policy. Fixture: a 100 m east-west target, a 20 m parallel source at 5 m, a 20 m
  north-south side source: with 10° only the parallel source contributes; assert
  provenance retains both rejected and accepted candidates.

### 3. Successor matcher and aggregation (`rnetmatch`)

* **Method/data shape.** `rnet_match()` accepts `sf` or GeoArrow line arrays, projected
  geometries only (spherical CRS is explicitly unsupported), `dist_tolerance`,
  `angle_tolerance`, and `trees="xy"` or `"x"`. Rust bulk-loads R*-trees, compares cached
  slopes, bounding-box overlap, exact line distance, and computes shared length; matches
  are keyed by 1-based `(i,j)` and accumulated in a `BTreeMap`.
  [R API](https://github.com/nptscot/rnetmatch/blob/7eecbc10088b5972206a0c84a6ef9aef990717ef/r/R/rnet_match.R#L1-L44),
  [Rust candidate search](https://github.com/nptscot/rnetmatch/blob/7eecbc10088b5972206a0c84a6ef9aef990717ef/rust/src/lib.rs#L14-L99),
  [R*-tree build](https://github.com/nptscot/rnetmatch/blob/7eecbc10088b5972206a0c84a6ef9aef990717ef/rust/src/trees.rs#L4-L50)
* **Aggregation.** `rnet_aggregate()` treats extensive values as
  `sum(value * shared_len/source_len)` and intensive values as
  `weighted.mean(value, shared_len/target_len)`; categorical proportions are computed
  separately. `rnet_aggregate_{extensive,intensive}` expose the same laws.
  [aggregation](https://github.com/nptscot/rnetmatch/blob/7eecbc10088b5972206a0c84a6ef9aef990717ef/r/R/aggregate.R#L21-L159)
* **Failure/unsafe defaults.** Rust uses strict `< angle_tolerance`; geographic
  distance is marked TODO/inaccurate, slope comparison is not wrap-aware, and bounding
  boxes are only a candidate test. There is no object validation. The `trees` choice is
  a performance trade-off, not semantic equivalence proof. Determinism is better than
  an unordered join because `BTreeMap` orders keys, but floating accumulation and input
  precision remain material.
* **Seam/fixture.** This is the strongest reusable SATN implementation candidate,
  behind a provider-neutral matcher interface and projected-CRS gate. Fixture: two
  collinear pieces whose overlap is split across components; assert one `(i,j)` with
  summed shared length, reject a 90° crossing, and compare x-only/xy-tree result sets.

### 4. Segmentation, clustering and path seeding (`corenet::corenet`)

* **Method/data shape.** Transform to EPSG:27700, split with `stplanr::line_segment`
  at 20 m, retain segments where `key_attribute > npt_threshold` (default 1,500),
  centroid them, DBSCAN with `eps=18`, `minPts=1`, keep the first centroid per cluster,
  intersect a 10 m buffer around the base network, optionally group by `name_1` and
  add group centroid plus max-distance endpoints. Defaults: `maxDistPts=1,500 m`,
  `minDistPts=2 m`, `n_removeDangles=6`, `penalty_value=1`, `max_path_weight=10`.
  [corenet](https://github.com/nptscot/corenet/blob/aaed15de5c3a4da9ed3b1b5d5220fbd1a373a02f/R/corenet.R#L134-L212),
  [group seeding](https://github.com/nptscot/corenet/blob/aaed15de5c3a4da9ed3b1b5d5220fbd1a373a02f/R/corenet.R#L213-L259)
* **Path algorithm.** `calculate_paths_from_point_dist()` filters centroids by Euclidean
  distance, calls `sfnetworks::st_network_paths(type="shortest", weights="weight")`,
  sums edge weights and drops paths over `max_path_weight`; optional environment cache
  is keyed by sorted stringified point attributes.
  [path routine](https://github.com/nptscot/corenet/blob/aaed15de5c3a4da9ed3b1b5d5220fbd1a373a02f/R/corenet.R#L478-L599)
* **Failure/unsafe defaults.** `minPts=1` makes every point a cluster; first-row
  cluster selection and positional cache keys are order-sensitive; `max_path_weight`
  is a weight threshold, not metres; missing key attributes are converted to 0 and
  values <=50 become 0.01 in `prepare_network`. These are Scottish network-selection
  policies, not generic connectivity rules.
* **Seam/fixture.** SATN needs `seed_points` and `path_policy` contracts with explicit
  metric distance, deterministic IDs, tie-breaks, and no implicit value threshold.
  Fixture: three high-flow segments (two within 10 m, one 20 m away) and one low-flow
  bridge; assert clustering, seed count, and path rejection are policy-controlled.

### 5. Grid-density selection and weighted paths (`npt::cohesive_network`)

* **Method/data shape.** Reprojects, sets `value = base_value * 1.5` for A roads,
  `*1.2` for B roads, intersects a supplied grid, and chooses per-cell `top_n`: 6 for
  density <10, 10 for 10–<20, otherwise `floor(density/10)+12`; it excludes lines
  within 10 m of the highest-value centroid and takes the first `top_n`. It keeps the
  90th percentile (`min_percentile=0.90`) of values, prepares an undirected weighted
  network, routes each selected centroid to all centroids, deduplicates geometry, and
  returns the largest component.
  [cohesive_network](https://github.com/nptscot/npt/blob/86662723f5b1cd26b0dca867ab36960a8c34de79/R/cohesive_network.R#L20-L130)
* **Failure/unsafe defaults.** A/B multipliers, 90th percentile, density formula,
  10 m exclusion, and first-row `slice_head` are embedded policy; `st_intersection`
  can split lines and duplicate attributes; the local `path_cache` is recreated on
  every call, so it never caches. Largest-component selection discards disconnected
  evidence. Do not import as a generic “cohesive network” routine.
* **Seam/fixture.** SATN can offer `select_priority_segments` with injected score and
  density policies. Fixture: two grid cells with densities 5 and 25 and tied values;
  assert selected counts, stable tie-break by source ID, and that disconnected cells
  are reported rather than silently dropped.

### 6. Infrastructure classification (`osmactive::classify_cycle_infrastructure`)

* **Method/data shape.** A pipeline of `case_when` rules over OSM tags, distance to
  driving roads, shared-use tags and cleaned widths emits ordered categories: wide or
  narrow segregated track, off-road path, shared footway, painted lane, optionally
  mixed-traffic street. Defaults: `min_distance=9.9 m`, classification `"Scotland"`,
  `include_mixed_traffic=FALSE`; `is_wide()` defaults to 2 m and treats missing width
  as zero. [classification entry](https://github.com/nptscot/osmactive/blob/9b3a2089fc2ae5082c99e5edcae277fad987cf6d/R/osmactive.R#L303-L351),
  [Scottish rules](https://github.com/nptscot/osmactive/blob/9b3a2089fc2ae5082c99e5edcae277fad987cf6d/R/osmactive.R#L351-L541)
* **Failure/unsafe defaults.** Tag spelling/case and absent columns alter results;
  a 9.9 m distance and “missing width = narrow” are assumptions, not measurements.
  Names containing Path/Towpath/Railway/Trail force off-road classification. The
  labels and thresholds are Scottish Cycling by Design policy and must not be treated
  as universal infrastructure truth.
* **Seam/fixture.** SATN should emit `infrastructure_observation` with raw tags,
  normalized evidence, confidence and jurisdiction policy ID. Fixture: each rule's
  minimal tag combination plus missing width/distance; assert raw tags survive and
  policy changes do not rewrite source evidence.

### 7. Speed/traffic defaults and CbD LoS (`osmactive::level_of_service`)

* **Method/data shape.** `clean_speeds()` maps `national` to 70 mph motorway or 60
  mph otherwise, then defaults untagged highways to 10/30/40/60/70 mph by OSM class;
  `estimate_traffic()` maps classes to assumed volumes (motorway 20,000; trunk 8,000;
  primary 6,000; secondary 5,000; tertiary 3,000; residential/service/unclassified
  500; active-travel ways NA). AADT is binned at <1,000, <2,000, <4,000, >=4,000.
  LoS joins the packaged table on AADT, speed category and infrastructure, then
  applies fallback values and labels 0 as non-compliant or mixed-traffic depending on
  category. [speed/traffic](https://github.com/nptscot/osmactive/blob/9b3a2089fc2ae5082c99e5edcae277fad987cf6d/R/osmactive.R#L787-L946),
  [LoS](https://github.com/nptscot/osmactive/blob/9b3a2089fc2ae5082c99e5edcae277fad987cf6d/R/osmactive.R#L954-L1062),
  [table](https://github.com/nptscot/osmactive/blob/9b3a2089fc2ae5082c99e5edcae277fad987cf6d/inst/extdata/los_table_complete.csv)
* **Failure/unsafe defaults.** These are imputed planning assumptions, not counts or
  measured speeds. Missing AADT silently triggers class-based estimates; joins fail if
  columns do not match. Do not expose LoS as surveyed compliance or substitute local
  policy without a jurisdiction adapter.
* **Seam/fixture.** SATN should require an explicit evidence source or mark an estimate,
  carry units and confidence, and inject a provider-neutral safety matrix. Fixture:
  one road for each speed/AADT bin and infrastructure class; assert boundary bins and
  “unknown evidence” state rather than silent imputation.

### 8. Line matching and post-overline (`npt::post_overline`, `simplify_network`)

* **Method/data shape.** `post_overline()` joins OSM cycling lines to an aggregated
  route network with `rnet_join(dist=1, segment_length=10)`, sums flow multiplied by
  source piece length, takes max for gradient/quietness, then divides by target length.
  `simplify_network()` uses `dist=25`, `max_angle_diff=35`, `segment_length=20`,
  re-aggregates bicycle values by `length_y`, maxes gradient/quietness, rounds numeric
  outputs, and removes source lines whose points fall within a 30 m buffer.
  [post-overline](https://github.com/nptscot/npt/blob/86662723f5b1cd26b0dca867ab36960a8c34de79/R/post_overline.R#L1-L67),
  [simplification](https://github.com/nptscot/npt/blob/86662723f5b1cd26b0dca867ab36960a8c34de79/R/simplify_network.R#L21-L93)
* **Failure/unsafe defaults.** Length multiplication/division is correct only for
  extensive flow; max is not valid for every quality metric. 30 m removal can erase
  nearby but non-overlapping routes; output precision is set to `1e3` (millimetre
  precision in sf units) then rounded to integer. Angle and distance are unvalidated.
* **Seam/fixture.** Reuse the rnetmatch contract, not `rnet_join` directly. Fixture:
  target 100 m and two source pieces (30/70 m) with flows 10/20; assert 17 weighted
  flow, max gradient, and an unrelated parallel line remains published.

### 9. Network simplification: skeletonization and Voronoi (`parenx`)

* **Skeletonization.** Project to EPSG:27700, union/buffer lines (default radius 8 m,
  mitre join), optionally segment overlapping corridors, rasterize at `scale=1`, fill
  holes of `16*scale` pixels, apply `skimage.skeletonize`, remove pixel knots unless
  `knot=TRUE`, transform back, and optional Douglas–Peucker `simplify` (default 0).
  CLI defaults: `buffer=8`, `scale=1`, `segment=False`. [source](https://github.com/anisotropi4/parenx/blob/ad5bf6f4c761865b46c174c45dc7de849abb2dc1/src/parenx/skeletonize.py#L38-L59),
  [pipeline](https://github.com/anisotropi4/parenx/blob/ad5bf6f4c761865b46c174c45dc7de849abb2dc1/src/parenx/skeletonize.py#L133-L150),
  [frame](https://github.com/anisotropi4/parenx/blob/ad5bf6f4c761865b46c174c45dc7de849abb2dc1/src/parenx/skeletonize.py#L280-L309)
* **Voronoi.** Buffer at 8 m; segment boundary points at `scale=5 m`; Shapely Voronoi
  uses snap `tolerance=1 m`; retain lines > half-buffer from boundary and contained in
  the buffer, then collapse short knots. Defaults: `simplify=0`, `scale=5`,
  `buffer=8`, `tolerance=1`. [source](https://github.com/anisotropi4/parenx/blob/ad5bf6f4c761865b46c174c45dc7de849abb2dc1/src/parenx/voronoi.py#L29-L52),
  [pipeline](https://github.com/anisotropi4/parenx/blob/ad5bf6f4c761865b46c174c45dc7de849abb2dc1/src/parenx/voronoi.py#L112-L164),
  [frame](https://github.com/anisotropi4/parenx/blob/ad5bf6f4c761865b46c174c45dc7de849abb2dc1/src/parenx/voronoi.py#L214-L235)
* **Failure/unsafe defaults.** Raster resolution, hole size, 8 m road-width proxy,
  precision grid (0.1 m), knot removal and Voronoi envelope change topology. Methods
  are for visual/strategic simplification, not legal centreline or route authority.
  Skeletonization is usually faster; Voronoi handles intersections better but costs
  more, as the project documents. [method note](https://github.com/nptscot/networkmerge/blob/1004edd8104093a272d1588ecd69452496010f0b/methods.qmd#L259-L266)
* **Seam/fixture.** SATN may provide a `corridor_simplifier` capability whose output is
  explicitly non-authoritative and retains source IDs/coverage. Fixture: two parallel
  100 m lines plus a T-junction; assert connectivity, max displacement, and that a
  simplifier never replaces the governed source geometry.

### 10. Graph diagnostics (`corenet::net_eval`)

* **Method/data shape.** Diagnostics include buffered spatial coverage, zone connectivity
  (polygon adjacency graph), population/OD coverage, and OD directness/efficiency. The
  directness routine converts the network to an undirected weighted graph, samples OD
  points with a city-name-derived seed, caps pairs to 5,000–50,000 based on city area,
  assigns zero efficiency to unroutable pairs, and reports routing success.
  [diagnostic implementation](https://github.com/nptscot/corenet/blob/aaed15de5c3a4da9ed3b1b5d5220fbd1a373a02f/R/net_eval.R#L200-L415)
* **Failure/unsafe defaults.** City name is a hidden random seed; sampled OD pairs,
  zero-for-unroutable convention, 2 km local radius, 500 m buffers and 2011 population
  are comparability choices, not universal quality metrics. `st_intersection` and
  CRS/geometry validity can bias coverage. Use diagnostics as evidence with method
  metadata, never as a single pass/fail score.
* **Seam/fixture.** SATN should expose `network_diagnostics` with explicit seed,
  sampling frame, denominator, CRS, connectivity policy and missingness. Fixture:
  a four-node square with one disconnected OD point; assert component count, routable
  denominator, directness, and repeatability for a fixed seed.

## Licence, maintenance and policy boundary

`corenet`, `osmactive`, `networkmerge` and `rnetmatch` declare MIT licences; `parenx`
declares Apache-2.0. The `npt` repository carries AGPL-3.0. These licences and the
current commits are evidence of reuse terms, not a promise of support or API stability:
the packages are research/active development repositories with small maintainer teams
and several TODOs/implicit assumptions. Preserve attribution and licence notices in
any adapter. [NPT licence](https://github.com/nptscot/npt/blob/86662723f5b1cd26b0dca867ab36960a8c34de79/LICENSE),
[corenet licence](https://github.com/nptscot/corenet/blob/aaed15de5c3a4da9ed3b1b5d5220fbd1a373a02f/LICENSE),
[osmactive licence](https://github.com/nptscot/osmactive/blob/9b3a2089fc2ae5082c99e5edcae277fad987cf6d/LICENSE),
[networkmerge licence](https://github.com/nptscot/networkmerge/blob/1004edd8104093a272d1588ecd69452496010f0b/LICENSE),
[parenx licence](https://github.com/anisotropi4/parenx/blob/ad5bf6f4c761865b46c174c45dc7de849abb2dc1/LICENSE),
[rnetmatch licence](https://github.com/nptscot/rnetmatch/blob/7eecbc10088b5972206a0c84a6ef9aef990717ef/LICENSE)

## SATN recommendation

Adopt only the narrow, provider-neutral primitives: projected line segmentation;
candidate matching with explicit distance/angle/shared-length evidence; extensive and
intensive aggregation laws; deterministic graph/path diagnostics; and a clearly
non-authoritative corridor-simplification capability. Keep OSM tags, Cycling by Design
categories, NPT flow names, Scottish speed/AADT defaults, density thresholds and
privacy suppression in separate jurisdiction/policy adapters. Reject any routine that
silently imputes speed/traffic, discards disconnected components, depends on positional
row IDs, or treats a visual simplification as a governed route.
