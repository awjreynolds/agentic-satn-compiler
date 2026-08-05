# ADR 0015: Local Evidence Store CLI and operational lifecycle

- Status: accepted
- Date: 2026-07-28
- Issue: #193
- Related: ADR 0005, ADR 0009, ADR 0010, ADR 0011, ADR 0012, ADR 0013 and ADR 0014

## Context

ADR 0013 selects one workspace-local DuckDB+Spatial Local Evidence Store; it is
not a source of record or a second GeoPackage store.  ADR 0011 makes Evidence
Coverage a possibly disconnected set of BNG/source-layer partition attestations,
and ADR 0012 requires Scenario Iteration to consume immutable logical artifacts.
The command line needs to make those constraints ordinary operations without
exposing database administration, SQL, a daemon, a container, or downloads.

The existing `satn snapshot` and `satn compile` remain the validated reference
path during additive cutover.  This decision defines the first CLI surface only;
it does not alter snapshot-loader or compiler behaviour.

## Decision

### One small command surface and one deep module

Add one Typer subgroup whose commands call a single Local Evidence Store module.
Its interface is logical operations -- provision a checked local runtime, plan or
refresh coverage, inspect/verify logical artifacts, make an exact subset, and
resolve a named Evidence Coverage snapshot.  DuckDB connections, Spatial loading,
tables, SQL, RTree creation, staging and locks stay inside that module.  There is
one adapter, DuckDB+Spatial; a GeoPackage is generated only as an inspection
export, never read as a working store or fallback.

```text
satn evidence [--workspace PATH] [--store PATH] [--extension-cache PATH]
              [--format text|json] <command>

  init       [--extension-archive PATH]
  refresh    AREA [--source-export DESCRIPTOR]... [--replace-source LAYER]
                  [--expect-state SHA256] [--dry-run] [--rebuild]
  status     [--area AREA] [--state SHA256] [--verify] [--provenance]
  query      LAYER (--area AREA | --geometry GEOJSON | --bbox XMIN,YMIN,XMAX,YMAX)
                  [--predicate intersects|within|contains] [--where FIELD=JSON]...
                  [--field FIELD]... [--state SHA256] [--export-gpkg PATH]
                  [--replace-export]
  delete     --yes --expect-state SHA256
```

The group is intentionally shallow: `status --verify --provenance`, rather than
check, verify and provenance subcommands, and `refresh AREA`, rather than
council/database/admin verbs.  `AREA` is an Area Definition YAML.  It may name a council, several
councils, or an arbitrary coherent polygon; its cells are added as a set, so an
Oxfordshire Area Definition can be added beside B&NES with no intervening import.

A `--source-export` descriptor is a local, governed declaration of one already
received Source Export and its selected Ingestion Contract.  It supplies the raw
path, layer, release/effective-date/licence/CRS declarations and checksum expected
by ADR 0011.  It is never a URL.  It may be omitted only for a required layer whose
retained raw export and matching descriptor already validate; otherwise planning
reports exactly which layer/export is absent.  `refresh` validates the descriptor
and raw bytes before it plans or writes anything.

Path resolution is deliberately boring and deterministic:

1. An explicit path is made absolute from the invocation working directory.
2. Otherwise `--workspace` is the invocation working directory and the store is
   `<workspace>/.satn/evidence/local-evidence.duckdb`.
3. The extension cache defaults to `<store-parent>/extensions`.
4. Paths inside an Area Definition or Source Export descriptor resolve relative to
   that file.  The resulting absolute paths are operational provenance only; their
   canonical relative declarations and raw checksums supply identity.

There is no environment-variable search, inferred council store, or hidden global
cache.  A command prints the resolved store and extension-cache paths in text and
JSON output.

### Init and offline behaviour

`init` creates the parent directory and empty store registry only after it
finds a pinned Spatial extension.  With `--extension-archive`, it verifies the
archive's DuckDB version, OS/architecture and SHA-256 against the checked-in
runtime lock before copying it to the declared cache.  Without it, it verifies the
already-cached pinned artifact.  It then opens a throwaway connection, loads
Spatial, records the runtime-lock fingerprint and creates the empty transactional
schema.  Repeating the command with the same runtime is a successful no-op;
another version/platform is rejected, never replaced implicitly.  All commands make
**no network request** and never invoke DuckDB's extension installer.  A missing
archive/cache fails with guidance to obtain the pinned artifact by the separately
governed environment-setup process.  Refresh, query and store-backed compilation
consequently work offline.  `status --verify` performs the non-mutating runtime,
registry and schema check.

### Refresh, coverage and source replacement

`refresh AREA --dry-run` parses the Area Definition, expands its BNG 10 km cells,
validates supplied/retained exports and returns the exact plan: requested cells by
source layer, already-valid attestations, missing cells, proposed `NoData` or
`Explicit Unknown` records, source/contract fingerprints, bytes/count estimates
when known, and the resulting Evidence Coverage snapshot fingerprint.  It takes no
writer lock and writes no store, cache or manifest.

Without `--dry-run`, the same plan streams each layer/cell through the declared
Ingestion Contract, validates CRS/schema/checksum/feature identity and spatial
index, then commits its partition attestations and current-state manifest in one
transaction.  A failed operation leaves the prior current state usable.  Exact
same export/contract/content is a no-op; overlap and disconnected coverage reuse
their existing attestations.  The output names the new immutable Evidence Coverage
snapshot (the sorted ADR-0011 attestation set), called `state` in commands.  Store
bytes, row IDs and the active-pointer timestamp are not in that state identity.

A descriptor for a layer/cell that differs from the current source version is
refused by default.  Replacing it requires both
`--replace-source LAYER` and `--expect-state <current-full-fingerprint>`; the
command changes only the requested cells of that source layer.  Old partition and
state manifests remain addressable for historical Scenario Compilations.  It does
not assume a cross-export content match, silently update unrelated cells, discard
the old raw export, or invalidate another layer.  A missing raw old export makes a
historical state unverifiable rather than making it equivalent to the new export.

`status` always reports the current state plus per-layer/cell counts, available,
NoData, Explicit Unknown, missing and stale classifications.  With `--area`, it
also compares that area's exact requested cells; with `--state`, it reports that
historical immutable manifest rather than silently substituting current coverage.
`--provenance` includes complete Source Export, Ingestion Contract, partition
content and attestation fingerprints, checksums, declared CRS and retention paths.
It never dumps a database table or raw SQL.

`status --verify` repeats validation rather than repairing: pinned extension,
store schema, manifest fingerprints, raw-source checksums, contracts/CRS, RTree
capability and state dependency closure, plus exact coverage when an area is
supplied.  It distinguishes missing coverage from valid NoData and Explicit
Unknown; a later mandatory Scenario Compilation still fails closed on required
unknown evidence.

### Exact inspection and export

`query` is an inspection operation over one declared source layer and a named
state (current only when `--state` is omitted).  Exactly one BNG geometry selector
is required: an Area Definition boundary, a GeoJSON geometry, or four-number BNG
bbox.  `--predicate` defaults to `intersects`.  Each `--where` is equality only:
the field must be declared by that layer's Ingestion Contract and the value is a
JSON scalar; repeated options are ANDed.  `--field` selects declared attributes
plus mandatory stable feature identity, geometry, partition attestation and CRS
provenance.  There is no arbitrary SQL, expression language, table name or shell.

The result is deduplicated by source feature identity, sorted by it, and reports
the exact predicate, selector fingerprint, state, consulted attestations and row
count.  JSON is a machine-readable result envelope; text is a readable table plus
the same identity/provenance summary.  An empty exact result is success.  Output is
not limited unless the caller later requests an explicit limit contract.  Supplying
`--export-gpkg` writes precisely that inspected result as one generated GeoPackage
with its manifest beside it; it refuses an existing path unless
`--replace-export` is explicit and cannot target the store or a retained Source
Export.  No other working-store/export format is introduced here.

### Rebuild, delete, locks and exit status

`refresh --rebuild` first verifies that every retained raw Source Export needed for
the selected area is valid, then builds and validates a sibling DuckDB file before
atomic replacement.  It recreates physical tables and indexes from the same logical
attestations; it cannot select a newer export, change an Ingestion Contract or
change an existing state fingerprint.  It is therefore a recoverable physical
repair, not a database-maintenance surface.

`delete` is intentionally recoverable: after `--yes` and an exact current
`--expect-state`, it moves the store file and lock metadata to a timestamped
`<store-parent>/trash/` sibling and prints its restore location.  It never removes
Source Exports, descriptors, extension artifacts, Scenario Compilations, deployment
outputs or GeoPackage inspection exports.  There is no purge command in the POC.

The store has one local user.  A mutating command (`init` when creating a store,
refresh or delete) takes one non-blocking exclusive lock around its DuckDB
transaction; read commands use read-only connections and see only committed data.
The lock is released by the operating system if a process dies; no stale-lock
breaking protocol is needed for the POC.  Busy writers return `75`; a failed
transaction leaves the prior committed state usable.

Successful commands return `0`, Typer usage errors `2`, domain/validation failures
`1`, and lock-busy `75`.  JSON writes one result object to stdout with `ok`,
`command`, resolved paths,
`state` when known, diagnostics and `exit_code`; human logs/errors go to stderr.
Text returns the same facts without requiring a parser.

### Coexistence with snapshot, compilation and Scenario Iteration

The present commands retain their exact meanings:

- `satn snapshot AREA` remains the complete existing snapshot path.
- `satn compile AREA` remains the complete existing compilation and publication
  path; it neither opens nor refreshes the Local Evidence Store.

Only after the ADR-0012 equivalence slices pass may `satn compile` accept the paired
opt-in `--evidence-store PATH --evidence-state SHA256`.  Supplying one without the
other is a usage error; the coordinator verifies the exact historical state and all
required dependency manifests before it constructs a Scenario Compilation.  A
different Area Definition, Criteria Set, Network Selection Profile or decision
ledger is iterated by rerunning `satn compile` with the same pinned state.  It may
reuse only dependency-valid materialisations and still invokes the existing
publication validation and atomic writer.  It never advances to the current store
state, refreshes coverage or falls back to a legacy snapshot silently.

Before that point the flags are absent, not experimental aliases.  The store is
sidecar-only: fixture and B&NES/WECA/A4017 equivalence checks must show identical
canonical network IDs, source lineage, unknowns, gaps, diagnostics and published
semantics before the CRITICAL-risk snapshot loader changes.  Failure continues to
use the old path, never a partially verified materialisation.

## Acceptance examples

```console
$ satn evidence init --extension-archive vendor/duckdb/spatial.duckdb_extension
initialised: .../.satn/evidence/local-evidence.duckdb
runtime: duckdb 1.x / spatial <locked-sha256>

$ satn evidence --format json refresh deployments/banes/area.yaml \
    --source-export data/governed/open-roads-2026-04.yaml --dry-run
{"ok":true,"command":"refresh","dry_run":true,"missing_cells":[...],"state":"<sha256>"}

$ satn evidence refresh path/to/area.yaml \
    --source-export data/governed/open-roads-2026-04.yaml
refreshed: 18 missing cells; reused: 7; state: <sha256>

$ satn evidence status --area path/to/area.yaml --provenance
coverage: complete (18 present, 0 missing, 0 stale); state: <sha256>

$ satn evidence query os-open-roads/RoadLink --area deployments/banes/area.yaml \
    --where road_classification='"A Road"' --field road_number --export-gpkg build/a-roads.gpkg
42 features; state: <sha256>; GeoPackage: build/a-roads.gpkg

$ satn evidence refresh deployments/banes/area.yaml \
    --source-export data/governed/open-roads-2026-07.yaml
error: source version differs for os-open-roads/RoadLink; use --replace-source ... --expect-state <sha256>

$ satn compile deployments/banes/area.yaml --evidence-store .satn/evidence/local-evidence.duckdb \
    --evidence-state <sha256>
```

The last command is an acceptance target after cutover, not a command enabled by
this ADR alone.  Missing extensions and coverage fail with `1`; a concurrent writer
returns `75`; a dry run leaves the database and filesystem inventory unchanged.

## Implementation slices

1. Add pure runtime-lock, Source Export descriptor, partition/state-manifest and
   diagnostics schemas with canonical validation.  Test path resolution, JSON/text
   result parity, offline missing/mismatched extension and idempotent init.
2. Implement the deep store module's read-only status/verify/query seam, using
   tiny governed fixtures.  Prove exact spatial/attribute equivalence, feature
   deduplication, provenance and GeoPackage-as-output-only.
3. Add dry-run then transactional refresh.  Prove disconnected/overlapping cells,
   explicit NoData/Unknown, no-op repeat, checksum/CRS failure rollback and guarded
   source replacement.
4. Add one writer lock, recoverable delete and `refresh --rebuild`.  Prove lock
   contention, crash-safe prior state, state identity and that neither operation
   touches raw exports or extension cache.
5. Build sidecar materialisations and equivalence fixtures while keeping
   `satn snapshot`/ordinary `satn compile` unchanged.  Only then add the paired
   pinned-state compile route, prove it rejects stale/missing evidence and preserves
   current publication validation, and measure the ADR-0012 iteration gates.

## Rejected alternatives

- A `duckdb`/SQL shell, `vacuum`, table/index commands or a migration framework:
  those leak a shallow physical interface and invite database administration.
- One store per council or an automatic contiguous expansion: both contradict
  disconnected BNG/source-layer Evidence Coverage.
- Automatic extension/source downloads or a background refresh daemon: they make
  evidence state timing and provenance non-reproducible and defeat offline use.
- A GeoPackage working-store fallback: it creates duplicate mutable truth and
  refresh/index logic; GeoPackage remains an explicit generated inspection export.
- An unpinned `current` store for compilation: it would make Scenario Compilation
  depend on a mutable cache rather than the recorded Evidence Coverage snapshot.
