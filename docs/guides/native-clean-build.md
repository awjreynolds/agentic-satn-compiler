# Clean native elevation evidence build

`scripts/prepare_native_elevation.py` regenerates the native elevation GeoJSON
and manifest from a network snapshot and an offline EA DTM cache. It imports
the current checkout's `scripts.acquire_ea_elevation.load_tile` and
`sample_grid`; it does not fetch tiles or read earlier elevation evidence.

The inputs are the raw snapshot `network.geojson` and a cache containing
`receipts/*.json` plus `objects/sha256/*.tif`. Use the existing approved source
preparation and EA acquisition process to materialize those inputs. This guide
does not introduce source URLs or change those acquisition contracts. For the
historical B&NES and WECA inputs, the snapshot locations are:

- B&NES: `data/snapshots/banes-osm-open-roads-v1-2026-07-29/network.geojson`
- WECA: `data/snapshots/weca-classification-elevation-2026-07-31-v14-fp-20260731T092920522968Z-02-fp-20260809T123804416841Z-01-connected-context-20260906-envelope-02/network.geojson`

Before a run makes a live Jev/TypeSafe call or invokes a Codex specialist,
obtain explicit user approval for that run. State each destination (the live
Jev/TypeSafe service and/or the named Codex specialist) and the data categories
it will receive: SATN planning inputs, candidate routes, source evidence and
decision context, including any nonpublic context. A single approval may cover
the specifically named B&NES and WECA batch, but does not authorize future
runs. Offline source preparation, cached-tile sampling, manifest generation,
builds, and tests require no model approval. This is operational guidance;
compiler live mode does not itself enforce consent.

Run the preparation from the checkout whose code is being verified. Set
`EA_CACHE` to the raw tile cache created by the existing EA acquisition process,
and choose a new output directory for each run:

```sh
EA_CACHE=/path/to/raw-ea-dtm-cache
RUN_DIR=/path/to/new-run/native-elevation

uv run python scripts/prepare_native_elevation.py \
  --network data/snapshots/banes-osm-open-roads-v1-2026-07-29/network.geojson \
  --cache-dir "$EA_CACHE" \
  --output-dir "$RUN_DIR/shared-feeder-elevation-banes-v1"

uv run python scripts/prepare_native_elevation.py \
  --network data/snapshots/weca-classification-elevation-2026-07-31-v14-fp-20260731T092920522968Z-02-fp-20260809T123804416841Z-01-connected-context-20260906-envelope-02/network.geojson \
  --cache-dir "$EA_CACHE" \
  --output-dir "$RUN_DIR/shared-feeder-elevation-weca-v1"
```

Each output directory contains `elevation-evidence.geojson` and `manifest.json`.
The manifest records input hashes, cache receipts used, the 10 m sampling rule,
and separate counts for missing receipt tiles and decoded NoData. Only sampled
values from available tiles are emitted; no values are interpolated. The
command refuses to replace either output file if it already exists, so use a
fresh output directory when rerunning.
