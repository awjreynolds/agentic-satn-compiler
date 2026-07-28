# B&NES CLI compile performance — 2026-07-28

## Scope

This benchmark exercises the ordinary command-line proof-of-concept with the
smallest real deployment:

```console
PYTHONPATH=src .venv/bin/satn compile deployments/banes/area.yaml
```

The cold run starts without `build/compiled/banes`. The warm run immediately
repeats the same command against the validated publication. Both runs use the
checked-in `banes-osm-current` snapshot and local elevation evidence; no network
access, daemon, database, or hosted cache is involved.

Reference machine:

- MacBook Pro, Apple M3 Max (12 performance and 4 efficiency cores), 48 GB RAM
- macOS 26.5.2 arm64
- Python 3.12.13
- GeoPandas 1.1.4, Pyogrio 0.13.0, Shapely 2.1.2

## Result

| Run | Before | After | Improvement |
| --- | ---: | ---: | ---: |
| Cold compile and validated publication | 281.45 s | 87.31 s | 3.22× faster |
| Warm validated-publication reuse | 4.72 s | 4.70 s | unchanged |

The cold result is below the 120-second target. It also exceeds the alternative
2× improvement target.

The before and after publications both contain 75 connections, 91 gaps, and
54,545 map features. Their canonical sorted compact `network.geojson` SHA-256
is identical:

```text
bc1d356a8809c0dd916e201f897fc8bedf1ff6a2431c5c645fe75878bc94e315
```

This identity check covers the published geometries and properties, including
criteria, decisions, governed evidence references, gaps, and topography.

## Coarse phase timings

Timings are wall-clock observations from the normal progress log, supplemented
by a local function timer around the two diagnosed hot paths. They are coarse:
adjacent preparation work is grouped where the command has no finer progress
event.

| Phase | Before | After |
| --- | ---: | ---: |
| NCN evidence marking | 139.75 s | 0.67 s |
| Post-cross-spine access, topography, and finalization | 72.1 s | 22.9 s |
| Backbone assembly | 22.6 s | 17.7 s |
| Validated artifact publication | 20.4 s | 20.4 s |
| Snapshot loading | 2.9 s | 2.9 s |

The top three cold costs before the change were NCN marking, the grouped
post-cross-spine phase, and backbone assembly. Publication was close to the
third cost but did not justify adding a second serialization path once the
compiler was comfortably below target.

## Changes

Two repeated calculations accounted for most of the cold runtime:

1. NCN marking rebuilt and reprojected the same 20-metre route corridor for
   every road edge. It now builds the corridor once, uses the road spatial index
   to select intersecting candidates, and computes the unchanged directional
   50% overlap rule as one vectorized operation.
2. Topography reprojected the full governed elevation dataset for every
   compiled edge. It now reprojects the evidence once per profile build and
   projects each edge frame once. Sampling, thresholds, evidence bindings, and
   published profiles remain unchanged.

Focused regressions ensure the NCN path does not return to per-edge corridor
construction and elevation evidence is transformed once for a multi-edge
profile build.

## Warm reuse and invalidation

The warm run is faster because the CLI validates and reuses the complete
publication when the configuration, governed input manifests, dependency
fingerprint, compiler code, runtime, and output checks all still match. It does
not reuse intermediate in-memory graph state.

Any relevant configuration, snapshot, path-sensitive dependency, runtime, code,
or artifact-validation change invalidates that publication and performs the
normal cold compile. This is deliberate non-reuse: it keeps the local
optimization subordinate to governed inputs and publication validation.
