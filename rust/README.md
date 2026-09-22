# Native SATN compiler

This is the Rust implementation of the SATN compiler. It reads pinned GeoJSON
sources directly; the Python compiler is retained as a reference.

```text
pinned sources and area configuration
  → front end: source admission, indexed graph, corridor inventory
  → mechanical mid-end: prepared town/city connections and route candidates
  → backend: review map, GeoJSON, decision records and explicit unknowns
```

Build and run from the repository root, with the configured snapshot present:

```sh
cargo build --release --manifest-path rust/Cargo.toml --locked
./rust/target/release/satn-rs \
  --config deployments/banes/area.yaml \
  --output build/rust-banes \
  --mode mechanical
```

If `CARGO_TARGET_DIR` is set, the executable is under that directory's `release/`
instead. Progress goes to stderr and the final report goes to stdout. Open the
output directory's `index.html` to inspect the source baseline and candidates;
`summary.json` and `network.geojson` contain the corresponding data.

The mechanical foundation generates candidates; it does not claim to have chosen
a final network. Road classification does not establish current provision,
safety, access or adoption. Source-only A-roads remain visible with unresolved
topology. Candidate generation is recorded separately from alignment selection.
Urban attachment uses the configured scope. Prepared urban connections come
from graph adjacency; the legacy rural-backbone distance limit does not cap
their routed length.

Each prepared connection searches the same directed graph four times: `direct`
uses measured edge length, `strategic-spine` uses `0.35 × length` for an A-road
reference and `1.6 × length` otherwise, `ncn-informed` uses `0.4 × length` for
an edge supported by current or context-derived cycle-route evidence and
`1.3 × length` otherwise, and `low-traffic` uses `0.75 × length` for the
configured low-traffic highway classes and `4.0 × length` otherwise. The
reported `length_m` is always the measured source length; `search_cost_m` is
the role's mechanical search cost. Identical ordered edge paths retain their
additional role names in `role_aliases`.

Context-derived NCN evidence is computed in native projected metres with a
20m buffer and a 50% edge overlap share. The projection is the explicitly
frozen gridless WGS84 to BNG Helmert fallback (`GRIDLESS_BNG_PROJECTION_POLICY`);
the Rust compiler does not claim OSTN15 accuracy. Candidate topology is
graph-supported, while provision remains an explicit `unknown` until a later
evidence or judgment stage.

Run the focused foundation check with:

```sh
cargo test --manifest-path rust/Cargo.toml --test foundation
```

The remaining integrated classifier, reasoning, replay and publication work is
tracked in [the Rust compiler roadmap](https://github.com/awjreynolds/agentic-satn-compiler/issues/538).

To run the compact live decision mid-end, keep the history directory separate
from the rendered bundle and opt into the one-shot specialist explicitly:

```sh
TYPESAFE_API_KEY=... \
./rust/target/release/satn-rs \
  --config deployments/banes/area.yaml \
  --output build/rust-banes-live \
  --history build/rust-banes-history \
  --mode live \
  --specialist-model gpt-5.6-luna \
  --specialist-reasoning-effort max \
  --allow-provisional
```

The live run writes `planning.json` and append-only `history/` records. Jev is
asked first for each admitted connection; an explicit unresolved Jev outcome
can invoke the configured Codex process once for a typed provisional proposal.
Replay reads those retained operations without launching either provider:

```sh
./rust/target/release/satn-rs \
  --config deployments/banes/area.yaml \
  --output build/rust-banes-replay \
  --history build/rust-banes-history \
  --mode replay
```

`--allow-provisional` is required for a specialist proposal to select an
alignment; otherwise the typed result remains unresolved.
