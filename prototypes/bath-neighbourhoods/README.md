# Bath neighbourhood candidates (throwaway prototype)

Open `index.html` directly in a browser. It embeds the generated geometry and has no network or map-service dependency. The polygons, official classified lines and three retained Bath entry approaches can be toggled; clicking a feature opens its source details. The entry popup lists the measured candidate intersections along that retained approach.

Regenerate from the isolated worktree with:

```sh
cd /private/tmp/satn-bath-neighbourhood-prototype
/Users/awjre/Work/banes-satn/.venv/bin/python prototypes/bath-neighbourhoods/generate.py --source-root /Users/awjre/Work/banes-satn
```

The generator reads these existing artifacts from `--source-root`:

- `data/snapshots/banes-osm-open-roads-v1-2026-07-29/official-road-classification.geojson` — only `a-road`, `b-road` and `classified-unnumbered` features enter polygonization.
- `data/snapshots/banes-osm-open-roads-v1-2026-07-29/osm-place-features.geojson` — Bath place extent, relation `5342409`, is the candidate display filter.
- `build/rust-rebuild/urban-entry-banes-corrected/planning.json` — current retained rural paths and their Bath crossing records.

## Measured run

The generated review used 2,663 official line features and polygonized 186 complete planar polygons. Seventy-four polygons intersect the Bath extent and are kept whole; the generator applies no area cutoff, clipping, snapping, subdivision or other closure source. The local line layer contains 929 complete official features touching the Bath extent or a retained candidate boundary. Three corrected entry paths are shown, with 24 positive-length route/polygon overlap intervals in recorded path order. They end at their recorded Bath entry crossings; the corrected artifact has no onward urban route from those crossings through Bath, so none is drawn or inferred.

The measured generation time was 0.158 seconds, with an 847,519-byte HTML output. These counts and timing are for this prototype run and can change if the pinned inputs change.

## Limits

Each polygon is a planar enclosure from official classified-road geometry, shown as a candidate only. It does not establish a neighbourhood intervention, connected internal street fabric, a safe crossing, modal access permission or cycle provision. Raw polygonized closures can be nested or overlap; they are retained as generated, including small areas. The rural paths are the existing corrected access geometries to Bath’s entry crossing, not routes through Bath. The older `banes-journey-evaluation` comparison is not used because its Englishcombe path predates the corrected urban-entry result.

This artifact is for a decision about future Rust integration. It is not production output, a route recommendation or an intervention claim.
