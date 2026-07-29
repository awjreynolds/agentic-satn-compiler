# Embedded-store benchmark — 2026-07-28

**Decision for #190: choose DuckDB + Spatial as the one Local Evidence Store engine.** Retain GeoPackage only as input/output interchange, not as a second query store. DuckDB returned exactly the same features for every measured predicate, is 2.41 times smaller, faster for the council and WECA-scale cases, and adds useful SQL joins/aggregation. The decision is conditional on pinned, offline provisioning of its wheel and Spatial extension.

This is a disposable prototype measurement, not an Evidence Refresh or compiler result. It did not invoke SATN compilation, snapshotting, WCS acquisition, publication, or the Wayfinder map.

## Inputs and method

| Layer | Features | Bytes | SHA-256 |
| --- | ---: | ---: | --- |
| WECA OS Open Roads | 59,872 | 30,452,575 | `87c944fb4c4f77c949f25913c58b3e7f49df80bbe0bf317606b32feb0653e89c` |
| WECA OSM network | 256,327 | 145,921,435 | `c30549bb9bd6a50de08f399a07589d4c757a0d2fcf5759beaa656716830b8508` |
| WECA EA elevation evidence | 277,092 | 124,210,029 | `8685af4585cccdd4fc2c2ed48404c40fe3f01ea9b709c0632899bdce91500567` |

Both candidates used the same normalized EPSG:27700 schema per layer: stable `feature_id`, canonical `properties_json` (every source attribute), and geometry. Queries use exact `intersects` after a bounding-box candidate filter. Result hashes cover sorted `(feature_id, geometry WKB, properties_json)` rows.

The shapes were a 4 km Bath urban window, the B&NES boundary, and the WECA boundary. Three newly spawned-process trials measure reopen-query behaviour; they do **not** claim an OS-page-cache-cold run. DuckDB has three same-process warm trials.

## Environment and provisioning

- Commit: `a53b7bcc0f99be47e0c3e9bc4b05a6a56503be8b`; Darwin 25.5.0 arm64.
- Python 3.12.13; GeoPandas 1.1.4; Pyogrio 0.13.0; GDAL 3.12.4; Shapely 2.1.2; SQLite 3.50.4.
- DuckDB 1.4.4; Spatial extension `f129b24`.

DuckDB was provisioned online into isolated `/private/tmp` directories using the pinned wheel and `INSTALL spatial`; the extension loaded successfully. A disconnected deployment must bundle/cache those exact artifacts. This is a mandatory implementation gate.

GeoParquet direct scan was not benchmarked: no GeoParquet writer (`pyarrow`) was installed, and adding a third distribution/layout was unnecessary to decide between the two indexed candidates.

## Build, storage and replacement

| Measure | GeoPackage | DuckDB + Spatial |
| --- | ---: | ---: |
| Normalize inputs | 21.180 s | 21.852 s |
| Import/write | 1.378 s (no-index paired write) | 0.851 s |
| RTree build | 0.541 s estimated* | 0.137 s |
| Total indexed build | 1.919 s | 0.988 s |
| Store bytes | 304,553,984 | 126,365,696 |
| Peak build RSS | 889 MiB | 1,146 MiB |
| Fresh B&NES-area replacement | 0.396 s | 0.239 s |

*GDAL/Pyogrio builds the GeoPackage RTree in its layer write path. The estimate is the paired no-index/indexed-write difference, not unsupported bare-SQLite index creation. The indexed GeoPackage verified `gpkg_rtree_index` metadata and `rtree_<layer>_geom` tables for all three layers. DuckDB created native RTrees on each `GEOMETRY` column.

The fresh B&NES replacement contained 10,776 roads, 41,305 network edges and 64,991 elevation points. It is a new-area build, not in-place mutation.

## Exact query results

Per-layer milliseconds, p50/worst of three reopened-process trials. Candidate counts, exact counts and result SHA-256s match in every one of the nine cells.

| Shape / layer | Exact features | GeoPackage p50 / worst | DuckDB p50 / worst |
| --- | ---: | ---: | ---: |
| Urban / roads | 2,299 | 22 / 25 | 60 / 61 |
| Urban / network | 6,987 | 45 / 51 | 94 / 94 |
| Urban / elevation | 7,778 | 45 / 47 | 135 / 136 |
| B&NES / roads | 10,776 | 666 / 679 | 148 / 148 |
| B&NES / network | 41,305 | 2,515 / 2,553 | 597 / 598 |
| B&NES / elevation | 64,991 | 1,251 / 1,274 | 1,338 / 1,339 |
| WECA / roads | 59,872 | 1,108 / 1,124 | 452 / 454 |
| WECA / network | 256,327 | 4,497 / 4,501 | 2,493 / 2,527 |
| WECA / elevation | 261,228 | 4,605 / 4,642 | 3,878 / 3,884 |

DuckDB warm p50s (three same-process trials) are urban 45–135 ms; B&NES roads/network/elevation 146/603/1,334 ms; and WECA 467/2,522/3,919 ms. Peak query RSS: GeoPackage about 426 MiB; DuckDB 473 MiB.

DuckDB meets the <=2 s partial-spatial-subset gate for urban and whole-B&NES work. WECA-wide network/elevation return effectively whole layers (256,327 and 261,228 features) and took 2.5–3.9 s. They are recorded as bulk-extraction observations, **not** claimed as passing the partial-subset gate. A future whole-WECA-under-two-seconds requirement needs pagination/streaming or shards independent of this engine decision.

## Equivalence proof

| Shape / layer | Candidate / exact | SHA-256 |
| --- | ---: | --- |
| Urban / network | 6,987 / 6,987 | `cb7f4490f299f59ccfea988b0ac927fc9cd0408a193e9e473287716350017818` |
| B&NES / network | 63,055 / 41,305 | `86afc6747f4c62a5a511e43652f0938d9777474ee519b6165dcf11c38ba5db96` |
| WECA / network | 256,327 / 256,327 | `27fe2380e3033518fe90efeee215870d46d87f5fd56ac04d02597e4705ae7b0b` |
| B&NES / elevation | 95,177 / 64,991 | `47546adf0e2e47713d664dac9107a39e23daf12a93edf75e801eaaa09bf36ccd` |
| WECA / elevation | 275,364 / 261,228 | `08b79da465fa12045189afcc8056f1f54a7934431175d4f5669f878dd2665a64` |

## Reproduction

The throwaway [benchmark script](../../scripts/benchmark_embedded_stores.py) has isolated `--child-build`, `--child-query`, `--child-warm-query`, and `--child-replacement` modes. Keep all stores, extension binaries and JSON output in `/private/tmp`.

```sh
PYTHONPATH=/private/tmp/banes-satn-embedded-store-benchmark/.duckdb-lib \
  /Users/awjre/Work/banes-satn/.venv/bin/python \
  scripts/benchmark_embedded_stores.py --child-build --build-engine duckdb \
  --source-root /Users/awjre/Work/banes-satn \
  --scratch /private/tmp/satn-embedded-store-benchmark \
  --duckdb-extension-dir /private/tmp/banes-satn-embedded-store-benchmark/duckdb_extensions
```

The all-in-one controller is not used for the reported data: source normalization peaks near 1.1 GiB and can compete with a spawned query child. The isolated modes give every measured build/query process its own RSS boundary.
