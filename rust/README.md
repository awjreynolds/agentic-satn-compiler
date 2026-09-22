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

Run the focused foundation check with:

```sh
cargo test --manifest-path rust/Cargo.toml --test foundation
```

The remaining integrated classifier, reasoning, replay and publication work is
tracked in [the Rust compiler roadmap](https://github.com/awjreynolds/agentic-satn-compiler/issues/538).
