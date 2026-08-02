# Reproduce the B&NES golden-path map

B&NES is the repository's sole flagship deployment: the real-world feature tour,
expected artifact set and quality baseline. This guide deliberately separates exact
snapshot reproduction from live reacquisition.

## What you need

- A clean repository environment completed through the
  [agent quickstart](../getting-started/agent-quickstart.md).
- Network access to GitHub for the governed B&NES source bundle.
- Approximately 4 GB free disk space for the environment, source bundle, compilation
  intermediates and generated artifacts.
- A desktop-class machine. The compile is substantially longer than the tiny fixture;
  progress events report the active stage and estimates where available.

No API key or live AI credential is required. The Area Definition uses Deterministic
Test Mode.

## 1. Acquire and verify the pinned source snapshot

Working directory: repository root.

```shell
uv run python scripts/acquire_banes_example.py
```

The script downloads one versioned public-source bundle, verifies its SHA-256, safely
extracts only the declared snapshot and then applies the compiler's normal snapshot
validator to every member hash. It refuses to replace a different target.

Success:

```text
verified B&NES snapshot: data/snapshots/banes-osm-open-roads-v1-2026-07-29
```

Rerunning is safe and validates the existing snapshot without downloading it again.

Do not edit a snapshot member, copy a partial directory or substitute current live
OSM data when trying to reproduce the named example. The released snapshot is the
completed retained-core target; reacquiring its historical parent is neither needed
nor part of this reproduction path.

## 2. Compile

```shell
uv run satn compile deployments/banes/area.yaml --full
```

Success: the command completes with a Reviewable Network and writes
`build/compiled/banes/`. The pinned example currently contains 56 compiled
connections and 93 explicit gaps. A gap is a valid review finding, not a crashed or
invented route.

Inspect these outputs before considering the run reproduced:

```shell
uv run python -c "from pathlib import Path; import json; root=Path('build/compiled/banes'); run=json.loads((root/'run.json').read_text()); assert run['status']=='reviewable'; assert run['connection_count']==56 and run['gap_count']==93; assert (root/'review-map/index.html').exists(); print(run['run_id'], run['status'])"
```

## 3. Open and review

```shell
uv run python -m http.server 8000 --directory build/compiled/banes/review-map
```

Use the [feature tour](../concepts/feature-tour.md) as the review checklist:

1. Strategic Active Travel Network and Places are visible by default.
2. Route cores distinguish existing, upgrade and proposed intervention states.
3. Halos identify the primary Alignment Basis.
4. Existing/upgradeable assets and unselected candidates can be added without
   replacing the strategic map.
5. Material endpoint gaps and officer/compiler divergence remain independently
   inspectable.
6. Hover and click expose evidence rather than requiring inference from colour.

## Exact reproduction versus fresh evidence

This guide uses the pinned bundle because live OSM, NCN and terrain sources change.
A fresh evidence run is a new snapshot and a new planning hypothesis; it must use a
new `snapshot_id`, record retrieval/effective dates and pass the same coverage and
licence checks. See [Build a new area](build-a-new-area.md) for that workflow.

## What this proves

It proves that a clean clone can reproduce the governed B&NES compiler input and
generate the complete review artifact set. It does not certify route condition,
legal access, land availability, engineering feasibility, detailed design, safety,
cost, consultation or adoption.
