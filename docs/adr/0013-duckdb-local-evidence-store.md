# ADR 0013: DuckDB is the Local Evidence Store

- Status: accepted
- Date: 2026-07-28
- Issue: #186
- Related: ADR 0005, ADR 0010, ADR 0011, ADR 0012 and issues #187 and #190

## Context

Scenario Iteration needs one useful local query surface over several evidence
families without repeatedly parsing large governed exports. The store is used by
one person through the command line. It is disposable and rebuildable; received
Source Exports and their checksums remain authoritative.

The representative benchmark normalised the same WECA Open Roads, OSM network and
elevation evidence into GeoPackage/RTree and DuckDB Spatial/RTree stores. All nine
layer/window queries returned identical feature counts and semantic result
fingerprints.

DuckDB was about 2.4 times smaller (126 MB versus 305 MB), built its indexed store
in about 1.0 second after source normalisation (versus 1.9 seconds), and was faster
for every measured query. Urban queries took about 45–135 ms and whole-B&NES
queries about 146 ms–1.33 seconds. WECA-wide network and elevation extraction took
about 2.5 and 3.9 seconds respectively; those operations return effectively the
whole layer and are bulk extraction observations, not the partial-subset
two-second gate.

GeoPackage has simpler installation and wider GIS interchange. DuckDB provides
the more valuable working interface for this proof of concept: typed SQL,
cross-family joins and aggregation, selective projection, reusable Edge
Enrichment queries, and direct scenario comparison without loading whole
GeoDataFrames.

## Decision

Use one workspace-local DuckDB database as the Local Evidence Store.

The database contains:

- a registry of Source Exports, ingestion contracts and full checksums;
- source-layer Evidence Partitions and their ADR-0011 attestations;
- typed, source-specific evidence tables in `EPSG:27700`, each with stable feature
  identity, partition identity and a DuckDB Spatial RTree on `GEOMETRY`;
- validated Edge Enrichment records and their complete dependency fingerprints;
  and
- logical materialisation manifests and diagnostics required by ADR 0012.

Disconnected councils are ordinary sets of BNG partitions in the same database.
Adding Oxfordshire does not import intervening geography. Overlapping Area
Definitions reuse existing matching partition attestations.

Evidence Refresh writes staging tables, validates source checksums, schema, CRS,
counts, partition attestations and spatial indexes, then commits the affected
partitions in one DuckDB transaction. A failed refresh leaves the previous
validated state available. A database-format or schema-contract migration builds
and validates a complete sibling database before atomic replacement.

DuckDB's `GEOMETRY` value does not carry authoritative CRS metadata. Every spatial
table and manifest therefore declares `EPSG:27700`; ingestion transforms once
under the versioned contract and validation rejects missing or different CRS
lineage.

Pin the DuckDB Python wheel and the matching Spatial extension version/platform.
The normal commands must not download extensions implicitly. Environment setup or
an explicit provisioning command installs the pinned extension into a declared
local cache; subsequent refresh, query and Scenario Iteration work offline. A
missing or mismatched extension fails with actionable guidance.

GeoPackage remains a generated portable GIS/export format. It is not a second
query store, fallback database or duplicate cache. GeoParquet is deferred until a
measured exchange or bulk-scan requirement justifies it.

## Interface and identity

The Local Evidence Store is one deep module. Its external interface will cover
only:

1. refresh requested Evidence Coverage from already-downloaded governed exports;
2. report and verify coverage/provenance;
3. query an exact spatial/attribute subset; and
4. resolve dependency-valid materialisations for Scenario Iteration.

Database connections, SQL, table names, RTree details, staging and extension
loading remain implementation details. Callers receive immutable logical records
or GeoDataFrames plus their manifests, never a live connection.

The database path, file bytes, row IDs, physical ordering, query plan and RTree
state never enter Source Export, partition, enrichment or Scenario Compilation
identity. ADR-0011 full canonical fingerprints and active ADR-0010 dependency
manifests are the reusable contract.

## Performance and correctness gates

- Exact partial spatial queries must return the same stable feature IDs,
  geometries and declared attributes as the governed-source reference query.
- Urban and whole-council partial queries must complete within two seconds on the
  declared reference machine. Full-region bulk extraction is reported separately.
- Refresh records source-normalisation, import, index, validation, disk and peak
  memory independently. The failed all-layers-in-memory prototype is a regression
  warning: production refresh streams or processes one source layer/partition at a
  time rather than retaining all WECA layers in Python memory.
- B&NES, WECA and the A4017 authoritative-classification fixture must remain
  semantically equivalent before the CRITICAL-risk snapshot loader can consume
  store-backed results.

## Rejected alternatives

- **GeoPackage as the working store.** It is an excellent export and the simpler
  spatial container, but is larger, slower in the representative benchmark and
  offers a weaker cross-family analytical interface for the intended scenario and
  enrichment work.
- **DuckDB plus GeoPackage as dual stores.** Two mutable stores duplicate schema,
  refresh, index and invalidation logic. GeoPackage is produced only at export.
- **PostgreSQL/PostGIS.** A server, installation and administration workflow is
  outside this single-user local tool.
- **One database per council.** It prevents straightforward overlap reuse and
  couples a scenario to an administrative packaging choice. Evidence Coverage is
  partition-based instead.
- **GPU acceleration.** The local Apple Silicon pipeline has no compatible RAPIDS
  path and no measured GPU-worthy stage.

## Consequences

DuckDB and its Spatial extension become pinned local runtime dependencies and need
an offline provisioning test. This is a small operational cost accepted for the
more useful query and analytical interface.

The first implementation remains additive. It builds and verifies sidecar store
materialisations and result-equivalence tests before changing `load_snapshot` or
compiler semantics. Existing snapshots and publications remain valid and provide
the reference path during cutover.
