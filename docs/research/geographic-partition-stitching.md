# Deterministic geographic partitioning and boundary stitching

Research for [#340 — deterministic geographic partitioning and boundary stitching for parallel SATN compilation](https://github.com/awjreynolds/agentic-satn-compiler/issues/340).

- Retrieved: 2026-08-05
- Scope: parallel compilation of the governed planning graph and evidence; no
  Scottish or regional policy assumptions
- Status: research note; **not an accepted ADR**

## Decision-oriented conclusion

Use a **stable projected grid as the semantic partition identity**, with a
read-only core/halo execution view and a deterministic global stitching pass.
Keep administrative areas as query/output groupings only. A scheduler may use
adaptive graph or space-filling-curve ordering to balance work, but that order
must never become a feature, edge, candidate, gap, or publication identity.

The first implementation should reuse the existing `bng-10km/v1` Evidence
Partition Key and `EPSG:27700` geometry contracts in [ADR 0011](../adr/0011-stable-evidence-partition-and-dependency-identities.md), and the
transactional/complete-with-gaps safeguards in [ADR 0013](../adr/0013-duckdb-local-evidence-store.md),
[ADR 0016](../adr/0016-local-evidence-performance-and-correctness-gates.md),
and [ADR 0006](../adr/0006-preferred-strategic-alignments-support-the-lcwip-pipeline.md).
Do not introduce an independently numbered partition scheme for compilation.

The unit of ownership is a stable logical feature/edge, not a clipped geometry
fragment. A feature that intersects several cells is read in every relevant
halo, but one deterministic owner emits it once. Boundary portals are explicit
references to stable graph nodes (or canonical boundary intersections when an
edge crosses a cell without a node). Workers may finish in any order; stitching
sorts by canonical IDs and validates continuity, directed access, CRS and
provenance before publication.

If a valid input has an unavailable optional cell, halo, or portal, the result is
still a typed `complete-with-gaps` compilation: the gap identifies the missing
partition/portal and carries an Evidence Request. It is never treated as a
successful route, silently omitted, or converted to favourable evidence. A
malformed required source or failed publication validation remains fail-closed
and leaves the last validated publication intact.

## What the primary sources establish

### Stable geographic addresses are better identities than administrative units

[ADR 0011](../adr/0011-stable-evidence-partition-and-dependency-identities.md)
already makes the relevant local decision: an Evidence Partition Key is
`{source_layer, partition_scheme, cell}`, version 1 uses the declared
`bng-10km/v1` grid, and a feature intersecting several cells is present in each
needed cell and deduplicated by stable source identity. Coverage is a set of
partition attestations, not a continuous council frontier. [ADR 0013](../adr/0013-duckdb-local-evidence-store.md)
also requires disconnected areas to coexist in one store and overlapping area
definitions to reuse matching attestations.

The maintained geospatial implementations support the same separation of
semantic address from execution ordering:

- Dask-GeoPandas' `hilbert_distance` maps geometry midpoints onto a Hilbert
  curve for spatial partitioning; its curve is constructed from supplied or
  computed total bounds and a chosen precision. That makes it useful for a
  **runtime sort/balancing hint**, but changing the dataset extent or precision
  changes the divisions, so it is not a durable identity
  ([GeoSeries.hilbert_distance](https://dask-geopandas.readthedocs.io/en/stable/docs/reference/api/dask_geopandas.GeoSeries.hilbert_distance.html)).
- OSRM's maintained MLD tool partitions a road graph into a hierarchy of cells,
  exposes balance, cell-size and boundary-source parameters, and permits traffic
  weights to be customised repeatedly without repartitioning
  ([OSRM command-line tools](https://project-osrm.org/docs/v26.6.1/tools),
  `osrm-partition` and `osrm-customize`). This is a useful precedent for
  separating topology/cell identity from changing edge weights; OSRM is not an
  authority for SATN policy or geometry.
- METIS defines a graph partition as an assignment of stable vertex positions to
  numbered parts and distinguishes edge cut from communication volume. Its
  interface vertices are exactly those adjacent to another partition, and the
  communication objective counts how many neighbouring partitions need a copy
  ([METIS 5.1 manual, §5.7](https://karypis.github.io/glaros/files/sw/metis/manual.pdf#page=23)).
  This supports measuring halo/portal cost, not replacing the governed grid
  identity with a run-dependent graph partition.

Administrative partitioning is still useful for a human query (“compile this
area”) and for publication grouping. It is a poor cache/ownership key because
boundaries can overlap, split a corridor, or change independently of the
underlying source cells; it also causes severe load imbalance for dense urban
and sparse rural areas. An administrative request therefore resolves to a
sorted set of grid cells before work is scheduled.

### Boundary retention must preserve graph continuity

OSMnx's maintained graph truncation API removes nodes outside a box by default,
but `truncate_by_edge=True` retains an outside node when at least one neighbour
is inside ([OSMnx `truncate_graph_bbox`](https://osmnx.readthedocs.io/en/stable/user-reference.html#osmnx.truncate.truncate_graph_bbox)).
The relevant principle is not the flag itself: a local view must retain enough
outside context to avoid turning a real crossing into a false dangling end.
The SATN equivalent is a halo containing every source feature and graph node
needed by the configured operation radius. Clipping an authoritative line at a
cell border is not a substitute for that context.

PostGIS `ST_Subdivide` offers a complementary implementation fact: a geometry
can be split by rectilinear lines, and an optional `gridSize` guarantees result
vertices on a fixed grid when inputs already satisfy that grid; subdivision is
primarily an index/query optimisation ([PostGIS `ST_Subdivide`](https://postgis.net/docs/ST_Subdivide.html)).
For SATN, any computational split must remain derived. The source geometry and
logical edge identity remain whole and are reassembled from the canonical edge
record; a subdivision vertex cannot create a new adopted route or source
identity.

### Stitching needs explicit interface records, not geometric coincidence

METIS calls vertices adjacent to another partition “interface (or border)
vertices” and notes that communication is incurred because those vertices must
be sent to each adjacent part ([METIS manual, §5.7](https://karypis.github.io/glaros/files/sw/metis/manual.pdf#page=23)).
That is the right abstraction for SATN portals. A portal is an immutable,
fingerprinted interface record containing:

1. the stable node ID, or a canonical quantised intersection along a whole edge;
2. the sorted pair of core partition IDs;
3. incident logical edge IDs and permitted directions;
4. the geometry-contract/CRS fingerprint; and
5. the source/graph snapshot and halo-contract fingerprints.

A portal is not a route choice and does not author a line. If two workers emit
the same portal, the merger keeps one record after exact canonical-key
deduplication. If their geometry, direction, or provenance disagrees, the
portal is `unknown`/invalid and the affected obligation receives a gap and an
Evidence Request rather than a guessed join.

## Proposed compilation contract

### 1. Partition identity and ownership

- Resolve an Area Definition to a sorted set of `bng-10km/v1` cells. Record the
  exact cell IDs and source-layer attestations in the run manifest.
- For each stable source feature/edge, compute the sorted set of cells whose
  core predicate intersects its canonical geometry. The owner is the
  lexicographically smallest cell in that set (or another explicitly versioned
  ownership rule); all other occurrences are halo references. Ownership is
  content-derived and independent of row order, process count, or scheduler.
- Preserve logical edge IDs and whole geometries. Never use a database row ID,
  filename, worker ID, geometric-nearest match, or clipped fragment as identity.
  These rules follow [ADR 0011](../adr/0011-stable-evidence-partition-and-dependency-identities.md)
  and its rejection of raw WKB, council keys, and automatic split/merge aliases.
- Emit each owned edge exactly once. A halo copy is read-only and must carry its
  owner ID; duplicate detection compares the full stable ID and canonical
  content fingerprint.

### 2. Core and halo

The core is the cell's owned input and output scope. The halo is the minimum
neighbouring context needed by a declared operation. The halo radius is not a
global magic number: it is the maximum support of the active profile (for
example, edge/portal snapping tolerance, candidate detour/search bound,
topography sample window, and evidence capture radius), rounded up to the
contract's metric units. A profile change changes the halo contract fingerprint
and invalidates dependent materialisations.

Workers may read halo edges and nodes to enumerate local paths, calculate
evidence, or identify portals, but may publish only records whose owner is the
worker's core or records explicitly typed as boundary diagnostics. Local path
enumeration must retain all bounded alternatives and rejection diagnostics; it
must not run a largest-component or dangle-removal heuristic that deletes a
satellite crossing. This is consistent with the finite candidate and explicit
gap behaviour in [`candidate_discovery.py`](../../src/satn/candidate_discovery.py)
and the non-deletion requirement in [ADR 0006](../adr/0006-preferred-strategic-alignments-support-the-lcwip-pipeline.md).

### 3. Boundary portals and duplicate edge ownership

Build a portal index from owned edges and halo references before candidate
selection. For a graph node already on both sides, use its stable node ID. For
an edge that crosses a grid border away from a node, retain the whole logical
edge and add a derived boundary-intersection record at the canonical
quantisation defined by the geometry contract. Do not alter the authoritative
edge geometry or invent a node ID.

The stitching invariant is:

```text
each stable edge ID has one owner;
each portal key has one canonical record;
every cross-cell path step names an existing directed edge and portal;
the two sides agree on endpoint coordinates, access, CRS and fingerprints.
```

Parallel edge records that describe the same logical edge are merged by stable
ID, not by approximate geometric overlap. A source split, merge, or changed
constituent sequence creates a new logical ID under [ADR 0011](../adr/0011-stable-evidence-partition-and-dependency-identities.md);
stitching must not alias it silently.

### 4. Cross-boundary obligations and gap propagation

Classify an obligation by the cells containing its canonical endpoints. Same-cell
obligations can be searched locally, subject to the halo. Cross-cell obligations
are global jobs whose search frontier is the deterministic portal graph. Each
portal transition carries direction and access evidence; a geometric touch
without an allowed directed transition is not continuity.

The global job must use the same finite path/deviation budgets as local
discovery. If the budget is exhausted, a required halo/partition is unavailable,
or a portal pair cannot be reconciled, return a typed gap with:

- obligation and endpoint IDs;
- sorted missing/invalid partition and portal IDs;
- the exact profile, halo and source fingerprints;
- deterministic diagnostics and Evidence Request IDs; and
- `complete-with-gaps` status.

If no permitted path exists after all required partitions are available, use the
existing `no-path`/Network Gap contract. If an optional evidence partition is
missing, preserve the candidate with `unknown` facts and request evidence; do
not score missing data as zero. This mirrors the existing result semantics in
[`discover_candidate_sets`](../../src/satn/candidate_discovery.py) and [ADR 0006](../adr/0006-preferred-strategic-alignments-support-the-lcwip-pipeline.md).

### 5. Deterministic stitching and publication

Workers return immutable partition results: owned records, halo references,
portals, candidate fragments, diagnostics, gaps, Evidence Requests, and a
content fingerprint. The merger:

1. sorts partitions, portals, edge IDs, obligations and candidate IDs by their
   canonical full value;
2. rejects duplicate stable IDs with differing canonical content;
3. validates every cross-cell portal and directed edge transition;
4. assembles candidate paths from ordered logical edge IDs and derives geometry
   from those IDs, never from worker order;
5. runs the existing deterministic candidate admission/selection contracts; and
6. validates the complete publication before atomically replacing the previous
   publication.

The semantic run fingerprint includes the source/partition attestations,
compiler and dependency manifests, profile/halo contract, sorted worker result
fingerprints and sorted gap/request roster. It excludes worker timing, process
count, machine path, task completion order, and cache/index layout. A scheduler
may use Hilbert ordering, METIS-like edge-cut estimates, or adaptive bundles as
performance hints, but rerunning with a different schedule must produce the same
canonical result or an explicit invalidation.

## Partitioning alternatives

| Strategy | Strengths | Failure mode for SATN | Decision |
| --- | --- | --- | --- |
| Administrative (council/Area Definition) | Familiar request and reporting boundaries; easy officer scoping | Unstable cache key; overlaps and disconnected areas re-ingest; corridors and graph components are cut at arbitrary borders; urban/rural load imbalance | Use only to resolve requested cells and group output |
| Stable grid (fixed BNG cells) | Stable identity, overlap reuse, deterministic ownership, offline evidence coverage, straightforward manifests | A long corridor crosses many cells; fixed size can over/under-load; requires halo and portal stitching | **Semantic default** (`bng-10km/v1` now; new size requires a new contract) |
| Hybrid grid + adaptive scheduling | Keeps stable identities while bundling cells by measured cost; can reduce communication and balance workers | Bundles can change; adaptive partitioning must not leak into identity or provenance; more scheduler metadata | **Recommended execution model**: grid owns data, bundles only schedule work |
| Pure graph partitioner (METIS/OSRM-style) | Can minimise edge cut/communication and balance graph work | Partition labels depend on graph/version/seed; hard to replay across source changes; boundary ownership becomes opaque | Optional benchmarked scheduler hint, never the identity or authority |

## Acceptance fixture and focused validation

Before implementation, add a small synthetic corpus with:

- one edge wholly inside a cell;
- one edge crossing a border at an existing node;
- one edge crossing at a non-node point;
- a duplicate feature present in two cells;
- a same-cell obligation and a cross-cell obligation;
- a missing halo, a conflicting portal, a disconnected island, and an
  optional-unknown evidence partition; and
- reversed input order and worker completion order.

The fixture should assert identical sorted IDs, geometries, candidate rosters,
portal records, diagnostics, gap/request IDs and run fingerprints across input
permutations and worker counts. It should also prove that a failed optional
partition yields `complete-with-gaps` and a valid publication, while a malformed
required artifact preserves the previous validated publication. The existing
partition identity and publication-equivalence gates in [ADR 0016](../adr/0016-local-evidence-performance-and-correctness-gates.md)
are the appropriate test seam; no full-suite benchmark is needed for this
research decision.

## Commands and local evidence checked

Read-only checks performed in the repository:

```console
sed -n '1,260p' docs/adr/0011-stable-evidence-partition-and-dependency-identities.md
sed -n '1,220p' docs/adr/0013-duckdb-local-evidence-store.md
sed -n '1,220p' docs/adr/0016-local-evidence-performance-and-correctness-gates.md
rg -n "partition|halo|boundary|portal|gap|unknown|determin" src/satn docs/adr
```

These checks confirmed that the repository already has stable BNG partition
attestations, transactional refresh, deterministic candidate IDs and explicit
Network Gap/Evidence Request states, but no generic core/halo or portal/stitching
contract yet. No source, test, runtime configuration, or Git state was changed
outside this research note.

## Sources

- [ADR 0011 — Stable evidence partition and dependency identities](../adr/0011-stable-evidence-partition-and-dependency-identities.md)
- [ADR 0013 — DuckDB local evidence store](../adr/0013-duckdb-local-evidence-store.md)
- [ADR 0016 — Local evidence performance and correctness gates](../adr/0016-local-evidence-performance-and-correctness-gates.md)
- [ADR 0006 — Preferred Strategic Alignments](../adr/0006-preferred-strategic-alignments-support-the-lcwip-pipeline.md)
- [OSRM command-line tools: `osrm-partition` and `osrm-customize`](https://project-osrm.org/docs/v26.6.1/tools)
- [OSRM maintained partitioner implementation](https://github.com/Project-OSRM/osrm-backend/blob/master/src/partitioner/partitioner.cpp)
- [Dask-GeoPandas `GeoSeries.hilbert_distance`](https://dask-geopandas.readthedocs.io/en/stable/docs/reference/api/dask_geopandas.GeoSeries.hilbert_distance.html)
- [METIS 5.1 manual, graph partitioning objectives and interface vertices](https://karypis.github.io/glaros/files/sw/metis/manual.pdf)
- [OSMnx `truncate_graph_bbox`](https://osmnx.readthedocs.io/en/stable/user-reference.html#osmnx.truncate.truncate_graph_bbox)
- [OSMnx maintained truncation implementation](https://github.com/gboeing/osmnx/blob/main/osmnx/truncate.py)
- [PostGIS `ST_Subdivide`](https://postgis.net/docs/ST_Subdivide.html)
