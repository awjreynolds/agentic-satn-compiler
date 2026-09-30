# Native compiler quickstart: clone to first map

This runs the current Rust compiler in mechanical mode on the pinned B&NES
snapshot. It makes no AI calls and does not reacquire live map evidence.

## Requirements

- Git, Python 3.12 or newer, and [uv](https://docs.astral.sh/uv/);
- Rust and Cargo; and
- CMake and a C++ compiler, needed to build bundled GEOS.

Network access is required for the clone, package installation, and the pinned
snapshot download. The compiler run itself uses only the downloaded snapshot.

## 1. Clone and install the snapshot verifier

Working directory: the parent directory in which the repository should be
created.

```shell
git clone https://github.com/awjreynolds/agentic-satn-compiler.git
cd agentic-satn-compiler
uv sync --frozen --all-groups
```

## 2. Acquire the immutable B&NES snapshot

Working directory: repository root.

```shell
uv run python scripts/acquire_banes_example.py
```

The script downloads the versioned public-source bundle, checks its SHA-256,
extracts only the declared snapshot, then validates the member hashes and
snapshot identity. The snapshot is stored at
`data/snapshots/banes-osm-open-roads-v1-2026-07-29`. Rerunning validates and
reuses an existing valid snapshot.

## 3. Build and compile in mechanical mode

```shell
cargo build --release --manifest-path rust/Cargo.toml --locked
./rust/target/release/satn-rs \
  --config deployments/banes/area.yaml \
  --output build/rust-banes \
  --mode mechanical
```

The output directory contains `index.html`, `summary.json`, and
`network.geojson`. Mechanical mode applies code-owned rules and generates
candidate paths without calling TypeSafe or a specialist model. It records
unknown provision and unresolved choices instead of presenting candidate
generation as an AI-selected final network.

## 4. Inspect the result

```shell
python3 -m http.server 8000 --directory build/rust-banes
```

Open <http://localhost:8000> and stop the server with `Ctrl-C`. The map is a
planning aid. It does not establish route safety, legal access, feasibility,
adoption, or funding.

For the bounded classifier and optional specialist flow, including the rules
that decide when each may act, read the [decision-process guide](../concepts/decision-process.md).
Live calls require explicit approval for the service and the data sent to it.

## Retained Python fixture

The tiny committed fixture is still available for checking the older Python
`satn` interface. This does not exercise the current Rust compiler or represent
the current public map:

```shell
uv run satn snapshot examples/fixture/council.yaml
uv run satn compile examples/fixture/council.yaml
uv run satn proving check
```

See the [historical implementations](../compiler-architecture.md#historical-implementations)
section for context on the retained Python path.
