# Contributing

## Development environment

Python 3.12 and [uv](https://docs.astral.sh/uv/) are required. Do not activate or
manually manage a virtual environment; run project commands through uv.

```shell
uv sync --all-groups
uv run satn --help
```

Use `uv add <package>` for runtime dependencies and `uv add --group <group>
<package>` for development dependencies. The `lint`, `test`, and `audit` groups are
included by the default `dev` group, and `uv.lock` is committed for reproducibility.

## Checks

Run the smallest test that exercises a change while iterating:

```shell
uv run pytest --no-cov tests/test_relevant_module.py
```

Before handing work off, run the relevant tooling targets:

```shell
make lint
make test
make build
```

`make test` enforces 80% branch-aware coverage across `satn` and `lcwip`. Use
`make test-fast` for a fail-fast local pass without coverage.

## Git hooks

[prek](https://github.com/j178/prek) runs Ruff, ty, file-integrity checks,
ShellCheck, secret detection, actionlint, and zizmor:

```shell
make hooks-install
make hooks
```

`make hooks` checks staged files, which keeps the formatter scoped to touched Python.
CI runs the non-mutating security and integrity hooks across the complete repository.
Repository-wide formatting is deliberately deferred because it would rewrite the
existing codebase independently of a functional change.

Hook revisions are pinned. Update them with a seven-day supply-chain cooldown:

```shell
uv run prek auto-update --cooldown-days 7
```
