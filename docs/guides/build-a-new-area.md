# Prepare a native SATN area

The Rust compiler consumes a pinned GeoJSON snapshot; it does not acquire or
refresh source evidence. Prepare and govern the snapshot separately, then point
an Area Definition at it. Do not copy another authority's identity or snapshot
ID.

## 1. Define scope and source identity

Record the area identity, the source snapshot directory and ID, the source
boundary and network, source classifications, and the retrieval date, publisher,
licence, and attribution for every input. Keep immutable source bytes and their
manifest together. Use a new snapshot ID when any governed input changes.

The Rust Area Definition currently reads `area_id`, `area_name`, optional
`deployment_id`, and these source settings:

| Setting | Effect |
| --- | --- |
| `snapshot_dir`, `snapshot_id` | Select the pinned snapshot directory. |
| `community_place_types` | Select place classes for community access planning. |
| `urban_scope_buffer_km` | Set the configured urban preparation scope. |
| `candidate_built_up_areas` | Optional separately sourced ONS built-up-area polygons for candidate neighbourhoods. |
| `national_elevation.path` | Optional elevation evidence file. |
| `compilation.max_connection_km` | Set the maximum connection distance used during preparation. |

Paths are resolved relative to the Area Definition file. See the Rust
`AreaConfig` implementation and the [native compiler reference](../../rust/README.md)
for the active contract. The larger Python Area Definition schema below the
legacy reference pages belongs to the retained Python compiler.

## 2. Keep source admission separate from route judgement

The native compiler retains source corridors even when they are not connected
to the routable graph. It can mechanically prepare candidates from the graph
under direct, strategic-spine, NCN-informed, and low-traffic cost rules. Those
rules create candidate paths; they do not establish route condition, provision,
safety, or policy priority.

The decision process may classify a bounded choice from the supplied candidates
and evidence. It cannot add a path, alter the frozen input, or turn unknown
evidence into a fact. An unresolved result remains explicit. Read the
[decision-process guide](../concepts/decision-process.md) before enabling live
providers; live calls require explicit approval for each named service and its
input data.

## 3. Compile and inspect

With Rust, Cargo, CMake, a C++ compiler, and the pinned snapshot available:

```shell
cargo build --release --manifest-path rust/Cargo.toml --locked
./rust/target/release/satn-rs \
  --config path/to/area.yaml \
  --output build/my-area \
  --mode mechanical
```

The output contains `index.html`, `summary.json`, and `network.geojson`. Inspect
source accounting, candidate roles, topology, access evidence, and unknowns.
Use live mode only when the decision process and data-sharing requirements have
been reviewed. The public map is a review artifact, not an adopted plan or
scheme design.
