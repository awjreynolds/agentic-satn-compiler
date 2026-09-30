# Review and publish a native deployment

The native compiler's mechanical output and its native-agentic publication are
different artifacts. Mechanical output is for local inspection. Live planning
or replay produces the decision-map bundle with `publication.json` that the
native publication validator expects.

## Build and inspect locally

With a pinned snapshot and Rust prerequisites installed, mechanical mode creates
a local candidate review bundle without model calls:

```shell
cargo build --release --manifest-path rust/Cargo.toml --locked
./rust/target/release/satn-rs \
  --config deployments/banes/area.yaml \
  --output build/rust-banes-mechanical \
  --mode mechanical
```

This output contains `index.html`, `summary.json`, and `network.geojson`. It is
not a native-agentic Pages publication. Live mode and replay create
`decision-map.json`, `decision-map.geojson`, `publication.json`, and the review
map. See the [decision-process guide](../concepts/decision-process.md) before
using a provider. Live calls require explicit approval for the named service and
the data it receives.

## Pages deployment

The [Pages workflow](../../.github/workflows/pages.yml) consumes a
`satn-pages.zip` release asset prepared before the workflow runs. It checks that
the selected GitHub release is published and neither a draft nor a prerelease,
extracts the archive, validates each packaged map with Chromium, then deploys
the validated tree. A rendering failure stops deployment. The workflow does
not acquire sources, compile a network, or create the ZIP.

The repository does not define a single native command that assembles
`satn-pages.zip` from the Rust publication. The exact preparation step for the
release tree and ZIP must therefore be treated as an external release input;
the Pages workflow verifies the supplied bytes. Do not describe
`scripts/package_pages.py` as the native package builder: it implements the
retained Python schema-2 packaging path.

Before including evidence in a public release, check the source licence,
redistribution terms, attributions, and publication disclaimer.
