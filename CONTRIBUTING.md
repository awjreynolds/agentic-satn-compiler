# Contributing

The current compiler is in `rust/`. The Python `satn` and `lcwip` packages,
their `make` targets, and their Area Definition schema are retained legacy
surfaces. Run checks for the implementation you change.

## Native Rust compiler

Install Rust/Cargo, CMake, and a C++ compiler. From the repository root:

```shell
cargo build --release --manifest-path rust/Cargo.toml --locked
cargo fmt --manifest-path rust/Cargo.toml --check
cargo test --manifest-path rust/Cargo.toml --locked --test midend
```

The test command above targets decision orchestration; choose the relevant test
target for your change. The Rust CI workflow pins toolchain `1.98.1` and runs formatting and the full
Rust test suite when Rust sources or its workflow change.

## Retained Python tooling

Python 3.12 and [uv](https://docs.astral.sh/uv/) are used by snapshot utilities,
documentation scripts, and the retained Python packages:

```shell
uv sync --frozen --all-groups
uv run ruff check .
uv run ty check src/
uv run pytest --no-cov tests/test_relevant_module.py
```

The `make lint`, `make test`, and `make build` targets operate on the Python
packages. `make test` enforces their branch-aware coverage baseline. Do not use
that legacy target as the Rust compiler's test gate.

## Repository hooks

`prek` runs the configured formatting, type, file-integrity, shell, secret,
GitHub Actions, and supply-chain checks:

```shell
uv run prek install
uv run prek run
```

The hook configuration is pinned in the repository. Review the exact checks
and their scope in the hook configuration when changing repository tooling.

## CI and focused checks

Choose the smallest test that exercises the changed behavior while iterating.
Before handoff, run the focused checks for the affected implementation. The
release Pages workflow validates packaged map rendering separately; it does not
compile or test source changes.
