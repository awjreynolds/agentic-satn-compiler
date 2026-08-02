# Agent quickstart: clone to first map

This is the supported first task for an unfamiliar coding agent. It uses committed
synthetic evidence, makes no network calls after dependency installation and finishes
in seconds on an ordinary development machine.

## 1. Clone and install

Working directory: the parent directory in which the repository should be created.

Prerequisites:

- Git;
- Python 3.12 or newer; and
- [uv](https://docs.astral.sh/uv/).

Network access is required for the clone and may be required the first time `uv`
fills its package cache.

```shell
git clone https://github.com/awjreynolds/agentic-satn-compiler.git
cd agentic-satn-compiler
uv sync --frozen --all-groups
uv run satn --help
```

Success: help lists `snapshot`, `compile`, `evidence`, `scenario`, `corpus` and
`proving`. Rerunning `uv sync` is safe.

## 2. Create the immutable fixture snapshot

Working directory: repository root.

```shell
uv run satn snapshot examples/fixture/council.yaml
```

Success: the command prints a path ending in
`examples/fixture/work/snapshots/fixture-001`. The snapshot contains five governed
source files and `snapshot.json`. Rerunning validates and reuses the immutable target.

If it fails, check the reported source path before rerunning. Do not create a partial
snapshot directory by hand.

## 3. Compile and publish the local result

```shell
uv run satn compile examples/fixture/council.yaml
```

Expected result:

```text
complete: 2 connections, 0 gaps
.../examples/fixture/work/output
```

Machine-check the important artifacts:

```shell
uv run python -c "from pathlib import Path; import json; root=Path('examples/fixture/work/output'); run=json.loads((root/'run.json').read_text()); assert run['status']=='complete'; assert all((root/name).exists() for name in ('network.geojson','network.gpkg','network-map.pdf','review-map/index.html','asset-accounting.json','divergence-records.json')); print(run['run_id'], run['status'])"
```

The output is about 2–3 MB. Compilation is atomic: a failed replacement does not
destroy the previous valid output. Rerunning an unchanged compile may reuse the
validated publication.

## 4. Inspect the map

```shell
uv run python -m http.server 8000 --directory examples/fixture/work/output/review-map
```

Open <http://localhost:8000>. Stop the server with `Ctrl-C`.

What this proves:

- the environment can read, snapshot and fingerprint governed evidence;
- the deterministic compiler can assemble and publish a network;
- the static review map and portable artifacts agree on their run identity; and
- no live AI, OSM, terrain or council system is required for the smoke test.

It does **not** prove that the synthetic route is suitable, adopted or representative
of B&NES.

## 5. Run the semantic acceptance example

```shell
uv run satn proving check
```

This compiles the checked-in composite decision scenario once and compares its
semantic JSON and SVG expectation. It is the fast test for reuse-first selection,
intervention state and deterministic fallback behaviour.

## Next

Follow [Reproduce the B&NES example](../guides/reproduce-banes.md). That path verifies
a real council-scale governed snapshot and shows the features the synthetic fixture
cannot demonstrate.
