# Local Evidence Store engine research — 2026-07-28

## Decision in brief

**Recommendation (inference): use one disposable, per-requested-area SQLite
GeoPackage store with a GeoPackage/SQLite RTree for each queried geometry layer.**
Build it from governed, checksummed source artifacts and replace it atomically on a
refresh; do not make it an authority or a long-lived merge target. This is the
smallest addition to SATN's current macOS/Python stack, which already pins
GeoPandas, Pyogrio and Shapely and already reads and publishes GeoPackages.

Store only the independently requested council/combined-area subset, rather than
eagerly materialising the roughly 2 GB national OS Open Roads source. A WECA-scale
extract (59,872 features / about 29 MB) and its associated OSM/elevation evidence
are appropriate first materialisations. This preserves disconnected council
coverage and makes a full rebuild an ordinary local operation.

Do **not** introduce a SQLite + DuckDB hybrid now. It would duplicate geometry,
indexes, lifecycle and provenance without an evidenced need. Reconsider DuckDB
only if the benchmark below shows that its native RTree or columnar scans have a
material, repeatable advantage for the real workload that GeoPackage cannot meet.

The <=2-second spatial-subset requirement is a benchmark acceptance criterion,
not a performance property that either format's documentation proves.

## Scope and decision constraints

This note answers issue #187. The fixed constraints are:

- local macOS/Python operation; no PostgreSQL, daemon, container or administrator
  installation;
- national OS Open Roads is about 2 GB; a WECA extract is 59,872 features / about
  29 MB; OSM and elevation artifacts also participate;
- subsets must be returned in <=2 seconds; coverage is requested independently by
  disconnected councils;
- the store may be discarded and rebuilt; authoritative exports and their
  checksums remain the source of truth; and
- one normal local user is the concurrency model.

The repository's accepted deployment model already treats GeoPackages and evidence
shards as generated process artifacts, retaining governed definitions and compact
manifests instead. Its publication path writes temporary sibling output and makes
the completed result visible with an atomic rename. Those are directly compatible
with this recommendation ([ADR 0005](../adr/0005-isolated-progressive-area-deployments.md),
[ADR 0003](../adr/0003-atomic-cited-lcwip-publication.md)).

## Evidence from primary sources

### SQLite + RTree + GeoPackage

- **Fact.** GeoPackage is an OGC, self-contained single-file SQLite container;
  its registered RTree extension uses SQLite's R*Tree virtual-table implementation
  and trigger set. The GDAL GeoPackage driver creates a spatial index by default
  for a newly written layer, and exposes whether a layer has one.
  [OGC GeoPackage standard](https://www.geopackage.org/spec140/),
  [OGC RTree extension](https://www.geopackage.org/guidance/extensions/rtree_spatial_indexes.html),
  [GDAL GeoPackage driver](https://gdal.org/en/stable/drivers/vector/gpkg.html)
- **Fact.** SQLite's RTree stores bounding rectangles, so it is a candidate filter:
  exact geometry predicates still need an exact check. Its default coordinates are
  float32, making a conservative envelope padding/false-positive exact recheck
  important. SQLite says RTree support can be omitted at build time; a runtime
  capability check is required before committing to direct use of SQLite RTree SQL.
  [SQLite R*Tree module](https://www.sqlite.org/rtree.html)
- **Fact.** SQLite permits multiple simultaneous readers but only one write
  transaction. An RTree cannot in general be updated while it is being scanned;
  the operation can return `SQLITE_LOCKED`.
  [SQLite transactions](https://www.sqlite.org/lang_transaction.html),
  [RTree read/write limitation](https://www.sqlite.org/rtree.html)
- **Inference.** This is well matched to one-user, build-then-query operation.
  A complete replacement build avoids long-lived incremental writes, RTree scan
  collisions, and any need to reconcile independently refreshed council slices.
- **Inference.** Packaging risk is low rather than zero: the repository already
  ships macOS Pyogrio wheels and uses GeoPackage through GeoPandas/Pyogrio, but the
  selected runtime must verify `rtree` support and GDAL GeoPackage reading in CI
  and on both supported macOS architectures. Do not create a GeoPackage RTree with
  bare Python `sqlite3`: its GeoPackage trigger functions come from the spatial
  stack, and Python warns macOS builds may lack loadable-extension support. Use the
  existing GDAL/Pyogrio path to write/maintain it. No new server is required.
  [Python sqlite3 documentation](https://docs.python.org/3/library/sqlite3.html),
  [GeoPackage 1.4 RTree requirements](https://www.geopackage.org/spec140/)

### DuckDB Spatial + GeoParquet

- **Fact.** DuckDB's Python client is installable from `pip`; persistent databases
  are a file passed to `duckdb.connect`. Spatial is a separately installed and
  loaded extension. Extension installation downloads a version/platform-specific
  binary into the user's DuckDB extension directory; the spatial extension bundles
  its own GDAL. Consequently, an offline/disconnected installation needs a pinned,
  pre-cached or explicitly distributed extension binary.
  [DuckDB Python API](https://duckdb.org/docs/stable/clients/python/overview),
  [Spatial extension](https://duckdb.org/docs/current/core_extensions/spatial/overview),
  [extension installation](https://duckdb.org/docs/stable/extensions/installing_extensions),
  [GDAL integration](https://duckdb.org/docs/current/core_extensions/spatial/gdal)
- **Fact.** DuckDB Spatial supports `CREATE INDEX ... USING RTREE (geom)`. It is
  used only for `GEOMETRY`, one of a listed set of intersection-implying predicates,
  and a planner-constant geometry argument. Building it after loading data uses a
  bottom-up bulk-loading algorithm; many changes/deletions may warrant rebuilding
  it. Index buffers that are read remain resident for the connection lifetime.
  [DuckDB RTree indexes](https://duckdb.org/docs/stable/core_extensions/spatial/r-tree_indexes.html)
- **Fact.** GeoParquet defines geometry metadata, CRS in PROJJSON, optional
  file-level and per-row bounding boxes, and WKB or GeoArrow geometry encodings.
  The per-row bounding-box covering is intended to allow readers to use row-group
  statistics before expensive geometry operations. DuckDB pushes projections and
  filters into Parquet, but usefulness depends on statistics/row-group layout.
  [GeoParquet 1.1 specification](https://geoparquet.org/releases/v1.1.0/),
  [DuckDB Parquet overview](https://duckdb.org/docs/stable/data/parquet/overview),
  [DuckDB Parquet tuning](https://duckdb.org/docs/stable/data/parquet/tips)
- **Fact.** DuckDB provides ACID transactions and snapshot isolation. In embedded
  read-write mode it permits a single process to read/write; multiple processes
  may read only in read-only mode. Concurrent modifications of the same rows can
  conflict.
  [DuckDB transactions](https://duckdb.org/docs/current/sql/statements/transactions),
  [DuckDB concurrency](https://duckdb.org/docs/current/connect/concurrency)
- **Inference.** It is the stronger analytical candidate for a larger-than-memory,
  column-pruning workload, or if sorted GeoParquet row groups with bounding-box
  columns make direct scans sufficient. It has more release/pinning work than the
  current GeoPackage stack and direct GeoParquet is an immutable-file workflow:
  replacing or appending partitions is simpler than feature-level update.

### Cross-cutting consequences

| Concern | SQLite GeoPackage + RTree | DuckDB Spatial + GeoParquet |
| --- | --- | --- |
| Spatial subset | Persistent per-layer RTree candidate pruning; exact predicate required. | Native RTree is available but has predicate/constant limitations; direct GeoParquet depends on bbox columns and row-group pruning. |
| Bulk ingest | Write layer, then ensure/verify GeoPackage index; benchmark the GDAL path. | Load rows first, then create RTree; DuckDB documents this as faster than indexing each insert. |
| Incremental region import | Prefer new area store and atomic replacement; trigger-maintained index is possible but not the normal path. | Append/replacement is natural for Parquet partitions; native DB updates are transactional but add an engine artifact. |
| Geometry and CRS | Standard GeoPackage geometry/SRS metadata; use the declared layer CRS and transform once at the boundary. | GeoParquet mandates explicit rules: missing `crs` means OGC:CRS84, while `null` means unknown. Preserve source CRS explicitly. |
| Transactions/concurrency | One writer, many readers; single user is compatible. | ACID/MVCC in one writing process; cross-process writers do not meet the no-daemon constraint. |
| Packaging | Reuses existing GeoPandas/Pyogrio/GDAL use; check RTree in the chosen SQLite runtime. | Add the `duckdb` wheel plus an extension binary per DuckDB version/platform, and an offline provisioning rule. |
| File size and portability | Portable, widely supported single-file interchange container; actual size is data/index dependent. | GeoParquet is portable columnar interchange; a DuckDB `.duckdb` file is an additional engine-specific cache. Actual size depends on encoding/compression/row groups. |
| Deterministic exports and provenance | Do not hash mutable database bytes as semantic truth. | Do not infer byte identity from Parquet or database writes. |

DuckDB's native `GEOMETRY` value does not itself carry CRS, so its candidate must
also carry a declared CRS column/metadata and pass source/target CRS explicitly to
transforms. [DuckDB Spatial functions](https://duckdb.org/docs/current/core_extensions/spatial/functions)

The final two rows are intentionally not format-performance claims. Neither
specification supplies a reproducible byte-serialization guarantee suitable for
governed identity. GeoPackage's default `gpkg_contents.last_change` timestamp and
DuckDB's documented non-deterministic result ordering make a byte-identical output
an explicit export policy, not an accidental property. For either candidate,
provenance should be a canonical adjacent
manifest containing source URI/version/licence, retrieval time, SHA-256, selected
feature/layer counts, area definition digest, CRS/transform policy, tool versions,
build command and output checksums. Deterministic *logical* export should sort by a
stable source feature ID and canonicalise the selected attribute schema before
hashing; pin versions and use `ORDER BY` (and, where needed, a fixed thread count).
The raw authority checksum remains the authority binding.
[DuckDB non-deterministic behaviour](https://duckdb.org/docs/current/operations_manual/non-deterministic_behavior),
[GeoPackage 1.4](https://www.geopackage.org/spec140/)

## Recommended physical shape

This is a narrow proposed cache contract, not an implementation:

```text
local evidence root/
  <area-id>/<source-family>/<source-sha256>/
    store.gpkg                 # generated spatial cache; per queried layer RTree
    provenance.json            # canonical build inputs, versions, counts, checksums
    source-manifest-ref.json   # pointer/digest to the governed authoritative artifact
```

**Inferences:** build in a sibling temporary directory; validate source digest,
CRS, feature count and spatial-index presence; then atomically rename the directory.
Never update a store in place across a source version. Council selection is an
input to the cache key, so an independently requested council does not couple its
availability or refresh to other councils. Use bounding-box index selection followed
by the exact geometry predicate in a declared working CRS (normally British National
Grid for metre-distance work); retain the original CRS and all source identifiers.

This gives OSM and elevation the same lifecycle without claiming that their schema
or geometry should be forced into one giant feature table. A GeoPackage can contain
separate layers plus ordinary provenance tables; the authoritative artifacts stay
outside it.

## Narrow benchmark shortlist

Run this before selecting an engine. Pin exact Python, GDAL/Pyogrio, SQLite,
DuckDB and extension versions, CPU architecture and input SHA-256s; run each case
three cold and ten warm times, reporting median and worst time plus peak RSS.

1. **A — recommended candidate:** area-scoped GeoPackage written through the
   existing Pyogrio/GDAL stack, with GeoPackage spatial indexes verified. Test the
   29 MB WECA Open Roads extract first, then a representative council subset made
   from the national source.
2. **B — analytical challenger:** DuckDB file with imported `GEOMETRY` table and
   a native RTree, with the same rows and exact predicate.
3. **C — immutable-file challenger, only if B is viable:** direct GeoParquet with
   explicit CRS and per-row bbox covering, spatially ordered before writing and
   queried through DuckDB. It tests whether no database file is needed; it is not a
   hybrid production design.

For A/B/C measure:

- exact `intersects` and `within` subsets for a small urban polygon, a whole-council
  polygon and a WECA-wide polygon; report candidate and exact-result counts;
- cold build, index-build, total output bytes, source-to-store size ratio, and the
  time to replace one council after a changed source checksum;
- repeated warm queries, reopening the process between cold trials, against the
  <=2-second worst-case target;
- concurrent one reader plus one replacement attempt, failed-import/crash recovery,
  and proof the previously complete store remains readable; and
- logical deterministic export hashes and a provenance-manifest diff after an
  intentional source, area or tool-version change.

Pass only a candidate that returns identical stable source IDs/geometries for the
same query, records its lineage, meets the <=2-second target on the declared
machine, and does not require network access after the environment is provisioned.
Do not use a synthetic point benchmark or the 29 MB extract alone to pass the
2 GB-source decision.

## Open questions before implementation

1. What is the precise initial query mix: feature lookup, bbox, polygon
   intersection, nearest, spatial join, raster sampling, or network construction?
   DuckDB's native RTree only accelerates a defined subset of predicate shapes.
2. What are the OS Open Roads layer schema, native CRS, stable feature identifier,
   licence/attribution terms, and real council-subset sizes in the governed source?
3. Are cache files allowed to be shared between Apple Silicon and Intel macOS, or
   must the reproducibility contract be logical/export-level only? Confirm the
   support matrix and pre-cache DuckDB Spatial if B/C proceeds.
4. Which OSM and elevation artifacts need spatial querying, what their refresh
   cadence is, and whether their source terms permit local derived subsets?
5. How much disk budget is acceptable per area and source version, including a
   temporary replacement build? Measure it; neither candidate's documentation can
   predict geometry-compression and index overhead for these layers.

## Sources and status

All external claims above use first-party project documentation or the governing
OGC/GeoParquet specifications, accessed 2026-07-28. This note separates documented
facts from design inferences and deliberately leaves measured performance, output
size and deterministic-byte properties as benchmark questions.
