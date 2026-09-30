# Native Area Definition reference

The current Rust compiler reads a compact YAML Area Definition. It is parsed by
`rust/src/config.rs`; the Python `satn.models.AreaDefinition` schema is a
separate retained implementation and is described in the
[historical implementations](../compiler-architecture.md#historical-implementations)
section.

## Fields read by the Rust compiler

| Field | Purpose |
| --- | --- |
| `area_id` | Stable area identity. |
| `area_name` | Display name used by the publication. |
| `deployment_id` | Optional deployment identity. |
| `source.snapshot_dir` | Snapshot root; relative paths resolve from the YAML file. |
| `source.snapshot_id` | Required nonblank snapshot directory name. |
| `source.community_place_types` | Place classes used for community planning; has a built-in default. |
| `source.urban_scope_buffer_km` | Urban preparation buffer; defaults to 2 km and must be non-negative. |
| `source.candidate_built_up_areas` | Optional separately sourced built-up-area GeoJSON. |
| `source.national_elevation.path` | Optional elevation evidence path. |
| `compilation.max_connection_km` | Parsed and checked as positive (default 15 km); it currently does not cap native routed connection length. |

Example:

```yaml
area_id: example-area
area_name: Example area
deployment_id: example
source:
  snapshot_dir: ../../data/snapshots
  snapshot_id: example-snapshot-2026-09-30
  community_place_types: [town, village, suburb]
  urban_scope_buffer_km: 2
  candidate_built_up_areas: ../../data/context/example-built-up-areas.geojson
  national_elevation:
    path: ../../data/context/example-elevation.geojson
compilation:
  max_connection_km: 15
```

The Rust reader ignores unknown YAML fields, but only the fields above have a
defined native effect. `max_connection_km` is validated but is not currently
used to cap routes. Snapshot provenance and validation must be handled when
the input bundle is prepared; pointing at a path alone does not verify its
authority or licence.

## Validate and compile

```shell
cargo build --release --manifest-path rust/Cargo.toml --locked
./rust/target/release/satn-rs \
  --config path/to/area.yaml \
  --output build/example-area \
  --mode mechanical
```

See the [build-a-new-area guide](../guides/build-a-new-area.md) for input
preparation and the [decision-process guide](../concepts/decision-process.md)
for the mechanical and classifier boundaries.

## Retained Python Area Definition reference

The following fields describe the separate Python `satn.models.AreaDefinition`
schema. They do not configure the Rust compiler unless a Rust field above
explicitly matches.

| Python field | Purpose |
| --- | --- |
| `area_id` or legacy `council_id` | Stable geographic compilation identity. |
| `area_name` or legacy `council_name` | Human-readable name. |
| `deployment_id` | Catalogue/publication identity. |
| `source` | Boundary, places, network, source exports and immutable snapshot identity. |
| `compilation` | Network, evidence, selection, topography and agent profiles. |
| `publication` | Atomic output destination, title, audience and presentation settings. |
| `atm` | Optional governed comparison reference and redistribution controls. |

The Python source block can declare boundary/place queries and buffers, source
adapters and remote endpoints, official road classification, current or
reclassified NCN services, national elevation, and retained-core migration
lineage. Its source hierarchy resolves claim evidence; it does not select a
route.

Python compilation profiles include maximum connection distance, Network
Selection Profile ordering and displacement rules, topography and other
evidence profiles, candidate/transition limits, and agent provider/review
settings. Council-specific thresholds belong in versioned profile data;
missing optional facts remain unknown. Python publication additionally has
workspace destination and public redistribution safeguards. These contracts
are retained in the Python implementation, not the native Rust reader.
