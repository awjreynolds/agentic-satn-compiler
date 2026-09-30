# Reproduce the native B&NES map

This guide uses the pinned B&NES input bundle and current Rust compiler. It
does not reacquire live OSM or cycle-route data. The default mechanical run
generates source accounting and route candidates without calling an AI model.

## 1. Acquire and validate the pinned snapshot

Working directory: repository root.

```shell
uv sync --frozen --all-groups
uv run python scripts/acquire_banes_example.py
```

The acquisition script verifies the archive SHA-256, extracts only the declared
snapshot, and validates the snapshot manifest and member hashes. It refuses to
replace a different target; rerunning against the valid target is safe.

## 2. Build and run the native compiler

Rust, Cargo, CMake, and a C++ compiler are required. See the [native compiler
guide](../../rust/README.md) for build details.

```shell
cargo build --release --manifest-path rust/Cargo.toml --locked
./rust/target/release/satn-rs \
  --config deployments/banes/area.yaml \
  --output build/rust-banes \
  --mode mechanical
```

The output contains `index.html`, `summary.json`, and `network.geojson`. Review
source corridors, generated candidate paths, topology, and explicit unknowns.
Candidate generation is not a claim that an alignment is safe, currently
usable, legally accessible, funded, or adopted.

## 3. Open the map

```shell
python3 -m http.server 8000 --directory build/rust-banes
```

Open <http://localhost:8000>. Use the [decision-process guide](../concepts/decision-process.md)
to understand deterministic rules, TypeSafe classification, optional specialist
proposals, validation, and replay. Live provider calls require explicit approval.
