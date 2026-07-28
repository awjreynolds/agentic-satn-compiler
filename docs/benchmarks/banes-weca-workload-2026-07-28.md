# B&NES and WECA workload benchmark corpus

Issue [#191](https://github.com/awjreynolds/banes-satn/issues/191) records a
reproducible workload corpus, not an optimisation claim. Timings use wall clock
(`/usr/bin/time -l`); no measurement means **unknown**, never pass.

## Current baseline table

| Workload | Corpus / observation | Budget | Status |
| --- | --- | ---: | --- |
| B&NES semantic identity | `banes-osm-current`: 125 places, 41,158 edges, 5,567 context features; 75 connections, 91 gaps and 54,545 map features; semantic SHA-256 `bc1d356a8809c0dd916e201f897fc8bedf1ff6a2431c5c645fe75878bc94e315` | equality | recorded |
| B&NES cold compile + publication | 87.31 s wall | <=120 s | pass |
| B&NES validated-publication reuse | 4.70 s wall | separate warm value | pass |
| B&NES cold stages | snapshot / NCN / backbone / post-cross-spine / publication: 2.9 / 0.67 / 17.7 / 22.9 / 20.4 s | diagnostic | recorded |
| WECA pinned no-publication compile | #125 core: snapshot load 12.056657 s, network compile 1,757.573137 s, Cross-Spine 0.077397 s; 2,211 root pairs and 52 authoritative connectors | <=600 s | exceeds budget; not a publication result |
| WECA historical full compile | 482 places, 256,327 edges, 22,241 context features; partial run reached publication at 4,801.6 s, but no final CLI result, wall time or RSS | <=600 s | not passed |
| WECA retained/elevation corpus | v10 manifest `1993f2f66aaf9fabf95bb5621502a0b8d17430e1d12b49abfc0f03d61d830729`; 2,038,084,001 bytes | input corpus | recorded, not timed |
| Evidence Refresh / retained routes | 14,362 route features; 301,981 requests; 277,092 evidence samples; 24,889 NoData; 117 cross-boundary samples | retain NoData | recorded, not timed |
| Scenario Iteration | no current wall measurement | <=60 s | gap |
| Survey-attribution subset proxy | existing in-process workload: 90,000 samples × 100 polygons, 1.53 s real; 90,000 index candidates rather than 9,000,000 full scans | diagnostic proxy | pass for this proxy only |
| Local Evidence Store spatial subset query | GeoPackage/DuckDB subset extraction | <=2 s | open in #190; no result here |
| Current WECA deployed artifact set | reviewable; 269 connections/734 gaps; 350,405,054 bytes; no associated complete timed run | <=600 s | gap |

The completed B&NES values are in
`docs/research/banes-compile-performance-2026-07-28.md`. Historical WECA
evidence in `docs/research/weca-scale-benchmark-2026-07-27.md` is explicitly
incomplete: its 4,981.7 s final heartbeat is not a completion time. The pinned
no-publication benchmark is `build/benchmarks/weca-cross-spine-baseline.json`;
it records no CPU or RSS value.

The 1.53 s result exercises only the existing in-process EA survey-attribution
spatial index. It is a useful deterministic workload/proxy, but does **not**
prove the Local Evidence Store GeoPackage/DuckDB spatial-subset gate; that
separate <=2 s acceptance result remains open in #190.

## Bound local artifact details (observed 2026-07-28)

These artifacts were read, not produced, for this issue. They may be ignored by
Git and must be re-inventoried with every new timed run.

| Artifact | Size / count | SHA-256 / identity |
| --- | ---: | --- |
| B&NES snapshot | 55,487,071 bytes | manifest `d54cd57ff2b1a92fab0b48a8b616cc4bcb8721b274024880d4ccc17b9a486c39` |
| B&NES network | 24,092,572 bytes / 41,158 features | `6ce1a76491d12f0e57bebba087dc94b89f466dfa91ef109f66c81a34bd5aae59` |
| B&NES elevation | 17,003,507 bytes / 44,742 features | `cc38fed4cfa62b324035bb165e5fe569897356c970d8d1f1f0aa51957f2c575a` |
| B&NES deployed directory | 153,808,351 bytes | `run-70f2d1f00cc5` |
| WECA network | 145,921,435 bytes / 256,327 features | `c30549bb9bd6a50de08f399a07589d4c757a0d2fcf5759beaa656716830b8508` |
| WECA retained sample routes | 1,494,646,203 bytes / 220,708 features | `b65008b3357609174d0cd1967418f81c9a9355b86ff143126b8886db88b20d9f` |
| WECA EA evidence | 146,361,146 bytes / 277,092 features | `fcf4e643b19072792355e737b39f3ff74333099823217aa7c109640307d181f5` |
| WECA EA ledger | 157,159,217 bytes | `764d320b699895e36a3e633ffe984595cf7fffd905921067ed6536489b5d3f7e` |
| WECA survey index | 3,722,384 bytes / 1,931 features | `fd4d61fad7dfe99ca3b9a6f25275cd367eded1405b25ac8fbeeec24f9e928647` |

The v10 elevation manifest binds `two-pass-fixed-point/v1`, pre-elevation
fingerprint `fe04e843ab59668155119741225e5c0c2b1f0054e9099680507b078b84bda9b5`,
and output fingerprint `8685af4585cccdd4fc2c2ed48404c40fe3f01ea9b709c0632899bdce91500567`.
Sample validation is partial: North Somerset has 4,846 NoData samples and the
routing buffer has 20,043. This is workload evidence, not a completed refresh.

## Reproduction procedure

Use a disposable worktree with input snapshots and local EA evidence copied or
mounted read-only. Do not delete a live publication to make a cold run. Record
commit, machine, dependency versions, command exit status, logs, peak RSS and
inventory before/after every run.

```shell
# Read-only: no compiler, snapshotter, downloader or publisher is invoked.
.venv/bin/python scripts/capture_workload_inventory.py \
  --deployment banes --deployment weca \
  --snapshot banes-osm-current \
  --snapshot weca-classification-elevation-2026-07-28-v10

# First command has a disposable build/compiled/banes absent; repeat unchanged.
/usr/bin/time -l env PYTHONPATH=src .venv/bin/satn compile deployments/banes/area.yaml
/usr/bin/time -l env PYTHONPATH=src .venv/bin/satn compile deployments/banes/area.yaml

# Production survey spatial index; no network or compiler side effect.
/usr/bin/time -l .venv/bin/python -m pytest -q \
  tests/test_ea_elevation_acquisition.py::test_spatial_survey_attribution_scales_without_scanning_every_polygon
```

For Evidence Refresh, time each stage separately: bootstrap snapshot/compile,
EA acquisition, retained-core snapshot, then final compile/publication. This
issue intentionally did not run the live WCS acquisition or a full WECA build.

```shell
uv run python scripts/acquire_ea_elevation.py \
  build/compiled/weca-bootstrap/review-map/network.geojson \
  data/local/ea-lidar-dtm-1m-weca-samples.geojson \
  --cache-dir data/local/ea-dtm-cache --spacing-m 10 \
  --authority-boundaries data/local/weca-authority-boundaries.geojson \
  --survey-index data/local/ea-lidar-composite-dtm-1m-weca-survey-index.geojson \
  --weca-preflight --routing-buffer-m 15000 \
  --governed-input-fingerprint '<bootstrap-fingerprint>'
/usr/bin/time -l env PYTHONPATH=src .venv/bin/satn snapshot deployments/weca/area.yaml --retain-core
/usr/bin/time -l env PYTHONPATH=src .venv/bin/satn compile deployments/weca/area.yaml --full
```

## Next evidence required

- Successful WECA v10 cold compilation plus atomic publication, stage timings and peak RSS.
- EA acquisition and retained-core timings, cache status, tile/retry/NoData counts and fixed-point iterations.
- One timed governed Scenario Iteration with its decision-ledger digest.
- A read-only inventory beside every timed result.
