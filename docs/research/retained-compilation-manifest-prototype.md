# Retained compilation manifest and progress prototype

- Status: accepted Wayfinder prototype
- Date: 2026-08-05
- Issue: [#344](https://github.com/awjreynolds/agentic-satn-compiler/issues/344)
- Builds on: ADR 0011, ADR 0012, ADR 0013, ADR 0015 and Wayfinder issues #341–#343

## Purpose

This prototype makes automatic incremental and parallel compilation understandable
without introducing a workflow dashboard. A normal compiler invocation produces:

1. one concise progress stream for a person;
2. one machine-readable run report for diagnosis and CI; and
3. immutable Artifact Manifests for every retained result used by the publication.

The user asks the compiler to build an area. They do not select cache keys, resume
tokens, cells, processes or stitch order.

## Ordinary progress

```console
$ satn compile deployments/weca/area.yaml
SATN compile  area=west-of-england  mode=incremental  workers=auto(6)
Evidence      HIT      state sha256:6d51…  74/74 BNG cells validated
Area views    HIT      71  MISS 3          boundary/source changed: ST57NW, ST57NE, ST67NW
Network       HIT      68  BUILD 6         3 core cells + their dependants
Enrichment    HIT      68  BUILD 6         elevation and traffic profiles
Assembly      RUN      Bath, Bristol, North Somerset and South Gloucestershire
  Bath        DONE     19 cells  42 portals  0 gaps
  Bristol     DONE     22 cells  57 portals  1 evidence gap
  N Somerset  DONE     18 cells  33 portals  0 gaps
  S Glos      DONE     15 cells  29 portals  0 gaps
Stitch        DONE     161 portals  2 targeted extensions  1 explicit gap
Selection     HIT      scenario sha256:92b0…
Presentation  BUILD    review-map assets changed
Publish       DONE     deployments/weca  atomic replace validated
Result        COMPLETE_WITH_GAPS  sha256:b59a…  02:41  peak 7.4 GiB
Report        .satn/runs/2026-08-05T211642Z-b59a.json
```

The fixed dispositions are `HIT`, `BUILD`, `RUN`, `DONE`, `GAP`, `FAILED` and
`SKIPPED`. A short reason is mandatory for every miss, forced rebuild, gap and
failure. Progress may arrive in worker-completion order; the retained result is
always assembled in canonical partition and artifact order.

`--explain-reuse` prints the resolved plan before execution and embeds the complete
dependency reasons in the run report. `--format json` emits structured progress
events rather than changing the work.

## Artifact Manifest

The Artifact Manifest is canonical JSON. Its full SHA-256 is the Retained Artifact
identity. This abbreviated example is for one assembly partition:

```json
{
  "schema": "satn.artifact-manifest/v1",
  "artifact_id": "sha256:4f89…",
  "kind": "assembly-partition",
  "contract_version": "assembly-partition/v1",
  "status": "complete-with-gaps",
  "scope": {
    "area_definition": "sha256:81ae…",
    "partition_system": "bng-10km/v1",
    "core_cells": ["ST57NW", "ST57NE", "ST67NW"],
    "halo": {
      "profile": "routing-halo/v1",
      "cells": ["ST47NE", "ST57SW", "ST57SE", "ST67SW"]
    }
  },
  "implementation": {
    "stage": "sha256:1e34…",
    "dependencies": "sha256:c288…"
  },
  "parameters": {
    "routing_profile": "sha256:532f…",
    "ownership_rule": "smallest-core-cell/v1",
    "portal_rule": "canonical-boundary-intersection/v1"
  },
  "upstream": [
    {"role": "canonical-network", "artifact_id": "sha256:037b…"},
    {"role": "edge-enrichments", "artifact_id": "sha256:17fe…"}
  ],
  "outputs": [
    {
      "role": "owned-fragments",
      "path": "outputs/owned-fragments.fgb",
      "sha256": "869f…",
      "bytes": 1895231
    },
    {
      "role": "boundary-portals",
      "path": "outputs/boundary-portals.parquet",
      "sha256": "535d…",
      "bytes": 42091
    }
  ],
  "validation": {
    "contract": "assembly-partition-validation/v1",
    "result": "passed",
    "checks_sha256": "f8d2…"
  },
  "diagnostics": {
    "owned_features": 12984,
    "portals": 42,
    "gaps": ["gap:missing-crossing-evidence:ST57NE:003"]
  }
}
```

The canonical identity includes:

- artifact kind and contract version;
- canonical stage implementation and dependency fingerprints;
- canonical parameters;
- sorted upstream artifact identities;
- semantic partition, coverage, halo and ownership identities;
- output content digests; and
- validation contract and deterministic diagnostics.

It excludes absolute paths, database bytes, timestamps, worker count, worker
identity, scheduler order, process/thread model, elapsed time and memory use.
`artifact_id` is calculated over the manifest with that field omitted. Arrays whose
order has no domain meaning are sorted by their specified stable key.

## Run report

The mutable observation of one attempt is separate from artifact identity:

```json
{
  "schema": "satn.compilation-run/v1",
  "run_id": "2026-08-05T211642Z-b59a",
  "area_definition": "deployments/weca/area.yaml",
  "mode": "incremental",
  "workers": {"requested": "auto", "selected": 6, "profile": "local-benchmark/v1"},
  "started_at": "2026-08-05T21:16:42Z",
  "finished_at": "2026-08-05T21:19:23Z",
  "result": "complete-with-gaps",
  "artifacts": [
    {
      "kind": "area-view",
      "scope": "ST57NW",
      "disposition": "build",
      "reason": "source-attestation-changed",
      "artifact_id": "sha256:a303…",
      "elapsed_ms": 3812
    },
    {
      "kind": "assembly-partition",
      "scope": "ST67NW",
      "disposition": "hit",
      "reason": "validated-dependency-closure",
      "artifact_id": "sha256:4f89…",
      "elapsed_ms": 44
    }
  ],
  "stitch": {
    "input_artifacts": ["sha256:4f89…", "sha256:7c42…"],
    "portals": 161,
    "targeted_extensions": 2,
    "gaps": ["gap:missing-crossing-evidence:ST57NE:003"],
    "result_artifact": "sha256:92b0…"
  },
  "publication": {
    "previous": "sha256:9961…",
    "candidate": "sha256:b59a…",
    "validation": "passed",
    "replacement": "atomic"
  },
  "peak_rss_bytes": 7945689498
}
```

The report explains what happened but cannot authorize reuse. A retained artifact
is a hit only when its manifest, every output digest, validation contract and full
upstream dependency closure validate.

## Reuse and recovery examples

### Presentation-only change

```console
Evidence–Selection  HIT    retained semantic publication sha256:92b0…
Presentation        BUILD  review-map.js fingerprint changed
Publish             DONE   validated atomic replacement  00:08
```

No network, routing, enrichment or selection stage runs.

### Corrupt retained output

```console
Network ST57NW      MISS   output digest mismatch; artifact quarantined
Dependants          BUILD  4 artifacts depend on sha256:037b…
Publish             DONE   previous publication served until replacement passed
```

Corruption is never silently accepted or repaired in place. The old bytes and
diagnostic are moved to a quarantine record; a fresh immutable result is created.

### Optional partition failure

```console
Assembly ST57NE     RETRY  worker exited unexpectedly; deterministic serial retry
Assembly ST57NE     GAP    retry failed; optional partition result recorded
Stitch              DONE   1 explicit gap and Evidence Request
Result              COMPLETE_WITH_GAPS
```

A malformed or unverifiable required governed input instead produces `FAILED`,
does not replace the publication and points to the preserved last valid result.

### Targeted rebuild

```console
$ satn compile deployments/weca/area.yaml --rebuild-stage enrichment
Evidence–Network    HIT
Enrichment          BUILD  forced for this invocation
Assembly–Publish    BUILD  descendants of forced stage
```

This does not delete historical artifacts. `--full` performs the corresponding
clean-room run and must produce the same semantic digest.

## Storage shape

```text
.satn/
  artifacts/
    sha256/
      4f/4f89…/
        manifest.json
        outputs/...
  quarantine/
    2026-08-05T211701Z-4f89…/
  runs/
    2026-08-05T211642Z-b59a.json
  publications/
    sha256-b59a… -> ../../artifacts/sha256/b5/b59a…
```

An artifact is built and validated in a sibling temporary directory, then made
visible atomically. The workspace lock protects manifest publication, not the
whole duration of independent worker computation. Every artifact reachable from a
publication, Scenario Compilation or active lineage is retained. Only unreferenced
artifacts become eligible for an explicit, dry-run-first garbage collection after
a configurable grace period; there is no silent size-based eviction in the POC.

## Prototype acceptance

The implementation is faithful when:

- an officer can identify hits, rebuilds, gaps and the final publication without
  understanding the DAG;
- CI can parse the same facts from stable JSON;
- two completion orders and one-versus-many workers produce identical Artifact
  Manifests and semantic publication digests;
- a UI-only edit demonstrably starts at Presentation;
- one changed partition rebuilds only its dependants;
- corrupt retained bytes are rejected and recovered without losing the last
  publication; and
- every published feature remains traceable through the final manifest DAG to its
  governed inputs.
