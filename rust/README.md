# Native SATN compiler

The Rust compiler is the current implementation. It reads pinned GeoJSON and an
Area Definition directly, builds a source-accounted graph, prepares route
candidates, runs the configured decision process, and publishes an inspectable
map plus machine-readable records. The Python compiler and its documentation are
retained historical references; see the [current decision-process guide](../docs/concepts/decision-process.md)
and [historical implementation notes](../docs/compiler-architecture.md#historical-implementations).

```text
pinned sources + Area Definition
  → admit source corridors and build the indexed graph
  → generate route candidates and apply mechanical rules
  → classify bounded choices when live mode is enabled
  → validate typed outcomes and retain unresolved work
  → publish the review map, GeoJSON, summary, and decision history
```

## Build a local review map

Build and run from the repository root with the configured snapshot present.
Building requires Rust, CMake, and a C++ compiler. The build links GEOS
statically; the resulting executable does not need a separate GEOS installation.

```sh
cargo build --release --manifest-path rust/Cargo.toml --locked
./rust/target/release/satn-rs \
  --config deployments/banes/area.yaml \
  --output build/rust-banes \
  --mode mechanical
```

Progress is written to stderr and the report to stdout. The output contains
`index.html`, `summary.json`, and `network.geojson`. The map presents source
baseline, generated candidates, access evidence, and unknowns. Mechanical mode
does not call a model. A candidate or road classification does not establish
current provision, safety, legal access, or adoption.

## What the mechanical rules do

For each admitted town/city connection, the compiler searches the same directed
graph under four named cost rules: `direct` uses measured edge length;
`strategic-spine` lowers cost on A-road reference edges and raises it elsewhere;
`ncn-informed` lowers cost where current or context-derived cycle-route evidence
supports the edge and raises it elsewhere; and `low-traffic` lowers cost for
configured low-traffic highway classes and raises it elsewhere. These costs
generate distinct candidate paths. They are search rules, not safety scores or
probabilities. `length_m` remains the measured source length;
`search_cost_m` records the rule-specific search cost. Identical paths retain
all role names in `role_aliases`.

Context-derived NCN evidence uses a 20 m buffer and 50% edge-overlap share in
projected metres. The current Rust projection is the frozen gridless WGS84 to
BNG Helmert fallback, not OSTN15. Source-only A roads remain visible even when
they cannot be matched to graph topology. Candidate topology and provision are
separate; provision stays `unknown` unless the evidence supports a later status.

Candidate-neighbourhood geometry is derived from a separately sourced ONS
built-up-area polygon. A candidate face must be fully inside that area and have
positive-length boundary frontages on at least two distinct official A, B, or
Classified Unnumbered roads; the built-up-area edge may close the remaining
sides. Road numbers, or an official road name for a Classified Unnumbered Road,
identify distinct frontages. Segment IDs and unnamed roads do not. Point
contacts are not frontages. The output reports provenance and measured area;
there is no size limit, and the geometry does not claim connected internal
streets, an existing low-traffic scheme, or safe access.

## Optional AI classification and replay

Live mode sends each prepared urban connection's frozen, compiler-authored
decision packet to the configured TypeSafe classifier. Rural decisions can
bypass classification when a single admissible candidate or a mechanical
dominance rule resolves the offered choice; otherwise they use the classifier.
The response must select an offered choice and pass schema, binding, and scope
validation before the compiler records and applies a typed operation. If the
classifier cannot support an outcome, an explicitly configured Codex specialist
may be called once for a provisional proposal; it cannot add facts or geometry,
and the compiler still validates its operation. Provisional choices require
`--allow-provisional`; otherwise the outcome remains unresolved.
The full rules and decision classes are in the [decision-process guide](../docs/concepts/decision-process.md).

Before a live run, obtain explicit approval for the named TypeSafe service and,
if configured, the Codex specialist, and for the planning inputs sent to each.
The CLI does not enforce that approval. Keep API keys in the environment and
never in tracked files or command-line arguments.

Use separate history and output directories. For a configured live run:

```sh
./rust/target/release/satn-rs \
  --config deployments/banes/area.yaml \
  --output build/rust-banes-live \
  --history build/rust-banes-history \
  --mode live \
  --specialist-model gpt-5.6-luna \
  --specialist-reasoning-effort max
```

The invocation above requires `TYPESAFE_API_KEY` to be set in the environment.
It does not permit provisional selection unless `--allow-provisional` is added.
Live attempts and typed operations are retained in the history directory.
Replay uses those records without launching either provider:

```sh
./rust/target/release/satn-rs \
  --config deployments/banes/area.yaml \
  --output build/rust-banes-replay \
  --history build/rust-banes-history \
  --mode replay
```

The Rust compiler's focused checks run with:

```sh
cargo test --manifest-path rust/Cargo.toml --locked --test midend
```

The GitHub Pages release workflow validates each packaged map in Chromium before
deployment. The successful current deployment is built from the native
publication path; it is not the old Python review-map publication.

## Offline officer scenario

Apply a separate attributable officer ledger to recorded planning history. The
configuration is required to regenerate community access after strategic choices:

```sh
./rust/target/release/satn-rs \
  --config deployments/banes/area.yaml \
  --output build/rust-banes-officer \
  --history build/rust-banes-history \
  --mode replay \
  --officer-decisions path/to/officer-decisions.json
```

For an exact admitted candidate binding, the ledger shape is:

```json
{
  "decisions": [{
    "decision_id": "example-decision",
    "connection_id": "connection-from-the-retained-run",
    "candidate_id": "candidate-admitted-on-that-connection",
    "source_refs": ["source-record-reference"],
    "attribution": "Illustrative officer decision",
    "rationale": "The sourced reason for this choice."
  }]
}
```

Use the actual retained IDs and attributable evidence; these strings are
placeholders. An omitted candidate may record an unavailable/unbound decision;
a candidate on another connection is rejected. The exact-source strategic-network
scenario has a separate typed representation in [`officer.rs`](src/officer.rs),
used by the ATM demonstration; it is not produced by assigning arbitrary geometry
to a candidate ID.

The output retains `officer-scenario.json` alongside the effective planning and
map artifacts. Baseline network/access and officer network/access have separate
layers. Changed community judgments remain unresolved unless the retained route
and parent/root bindings still apply. Replay leaves the original history intact
and makes no model calls. An illustrative officer scenario is not council adoption.
