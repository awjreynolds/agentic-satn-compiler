# WECA-scale compiler benchmark — 2026-07-27

## Scope and result status

This is runtime/no-regression evidence for the SATN compiler. It is **not** a
Bath–Saltford Preferred Strategic Alignment proving result, an LCWIP, an
adopted network, or a claim about route safety or feasibility.

The benchmark uses [config/weca.yaml](../../config/weca.yaml), a portable Area
Definition for the four West of England constituent-authority boundary queries.
It writes only to `build/benchmarks/weca-issue-137-compile`, not to the B&NES
publication directory.

The forced full compile reached its publication stage but did not emit its
final CLI result or atomically complete a publication manifest. It is therefore
recorded as an **interrupted/partial publication**, not a successful completed
benchmark. No completion, wall time, or peak memory is inferred from a
heartbeat or from files written before that interruption.

## Governed input binding

| Item | Observed value |
| --- | --- |
| Area | West of England Combined Authority area (`west-of-england`) |
| Snapshot | `weca-osm-current` |
| Snapshot manifest SHA-256 | `d4d8cbe37c13a6b9ae5d027693d64e89eab2edccf7b69afcdbec519883b1a988` |
| Snapshot retrieval time | 2026-07-24T15:21:01.845636+00:00 |
| Snapshot source | OSM plus configured NCN and reclassified-NCN services |
| Loaded boundary features | 4 |
| Loaded places | 482 |
| Loaded road-network edges | 256,327 |
| Loaded contextual features | 22,241 |

The historical `weca-osm-current` manifest explicitly records `null` for
official-road classification and elevation evidence. The configuration also
does not bind population-reach evidence, a school register, strategic education
admissions, or an assessment date/freshness policy for Network Selection.
Those omissions are intentional and explicit: this run must not be used as
evidence for a current Network Selection Profile decision.

## Invocation and environment

```shell
/usr/bin/time -l env PYTHONPATH=src \
  /Users/awjre/Work/banes-satn/.venv/bin/python \
  -m satn.cli compile config/weca.yaml --full
```

| Item | Observed value |
| --- | --- |
| Compiler commit at start | `3c6e9384e2e8057cad4cd4bc8219210c1c6c6467` |
| OS | macOS 26.5.2 (build 25F84) |
| Kernel/architecture | Darwin 25.5.0, arm64 (`T6031`) |
| Python | 3.12.13 |
| GeoPandas / Shapely | 1.1.4 / 2.1.2 |
| pandas / NetworkX / Pydantic | 3.0.3 / 3.6.1 / 2.13.4 |
| CPU model and installed RAM | unavailable in the sandbox (`sysctl` denied) |
| Wall/peak-memory measurement mechanism | macOS `/usr/bin/time -l`, process-lifetime high-water RSS |

The compile started at 05:15:53 local time. It loaded the snapshot at 05:16:05,
entered Backbone assembly after 4,771.6 seconds, and reached publication after
4,801.6 seconds. The last observed publication heartbeat was 4,981.7 seconds.
The process then became unavailable before its final CLI result and `/usr/bin/time
-l` summary could be collected. Wall time and peak RSS are therefore **unknown**,
not zero or estimated. The target remains under ten hours on this declared
environment; it is not passed until a process exits successfully within that
limit.

## Output and stage observations

| Observation | Value |
| --- | --- |
| Compile mode | forced full rebuild (`--full`) |
| Status | partial publication; final CLI/atomic publication result unavailable |
| Last observed stage | `publication` |
| Compiled connections / gaps | 268 / 733 (`reviewable`) |
| Pre-interruption written artifacts | `run.json`, GeoJSON, GeoPackage, PDF, review-map directory and ZIP |
| Missing atomic-publication artifacts | `compiler-run.json`, `publication.json`, index/manifest files |
| Network Selection evidence | absent from this configuration/snapshot |
| Road-classification/elevation evidence | absent from historical benchmark snapshot |

This is a scale and runtime slice only. Its limitations mean it cannot validate
alignment-selection population, education, existing-alignment or elevation
criteria; those require separately governed evidence inputs.

## Recorded B&NES baseline

The known completed B&NES baseline is retained for comparison, not as a result
of this WECA run: **125 places; 41,158 road edges; 5,567 context features; 75
connections; 91 gaps; `reviewable`; 272.3 seconds.** The currently available
B&NES publication confirms the 125 strategic-spine count, 75 connections, 91
gaps and `reviewable` status. Its peak memory was not measured in the evidence
available here and is therefore **unknown**.
