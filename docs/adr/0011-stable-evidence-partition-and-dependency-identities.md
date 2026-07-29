# ADR 0011: Stable evidence partition and dependency identities

- Status: accepted
- Date: 2026-07-28
- Issue: #188
- Related: ADR 0005, ADR 0009, ADR 0010 and issue #187's local-store research

## Context

Evidence Refresh must make independently requested, possibly disconnected council
areas available without making a Local Evidence Store file authoritative.  A
whole-store checksum, a council-name cache key, a database row ID, or raw GEOS WKB
would either couple unrelated areas or make provenance platform-dependent.  At the
same time, a Scenario Compilation must replay the exact evidence it used, and an
Edge Enrichment must be reusable only while its source-layer spatial partitions,
geometry, algorithm and parameters remain valid.

This is an identity contract, not a storage or GPU implementation. Physical storage
is selected separately by ADR 0013; the portable network-geometry contract remains
ADR 0009.

## Decision

### Canonical content identities

Every authoritative identity below is a full, lower-case SHA-256 digest of a
versioned canonical payload.  The payload is UTF-8 canonical JSON: keys are sorted
lexicographically, arrays retain only declared semantic order, separators are
compact, strings use the established ASCII-escaped JSON form, and `NaN`, infinity
and negative zero are rejected or normalised before serialisation.  Set-like arrays
are sorted by their full canonical value and duplicate values are rejected unless
the contract explicitly models a multiset.

New identity payloads contain no JSON floating-point values.  Parameters use
integers in a named base unit (`sample_spacing_mm`) or a normalised decimal string
where an integer unit is impossible.  An algorithm contract declares the
quantisation of any calculated result before it is fingerprinted.  This prevents a
runtime's float printer from becoming evidence semantics.

The full digest, not a filename or short prefix, is the identity.  A user-facing
short ID may be emitted, but records store and compare the full digest.  On loading,
the system checks that equal digests have equal canonical payload bytes; a short-ID
collision is lengthened or rejected, never silently joined.

`satn-network-geometry-v1` remains the identity for Network Place and network-edge
Point/LineString/MultiLineString geometry (ADR 0009).  Source and coverage geometry
uses `satn-evidence-geometry-v1`: source coordinates are transformed once at the
ingestion seam to `EPSG:27700` with an explicit always-X/Y transform policy;
identity coordinates are finite two-dimensional integer millimetres; negative zero
is zero; consecutive duplicate vertices are removed; lines choose the
lexicographically smaller direction; multipart members, polygon rings and holes are
canonicalised and sorted.  Ring orientation and starting vertex do not matter, but
exterior versus hole does.  Empty, invalid, collapsed or unsupported geometry fails
closed.  The original declared CRS and the transform are provenance, not an
unstated inference.  A non-British-National-Grid source needs a new declared
geometry-contract version; it must not substitute raw WKB.

### Source exports, features and partitions

A **Source Export** is the immutable authoritative artifact selected by governance,
not a download location.  Its fingerprint includes its source family/dataset/layer,
publisher release and effective date, licence, format and declared CRS, and the
SHA-256 of the received raw bytes.  Retrieval time, local absolute path, file name
and database location are retained as provenance but do not change the identity.

An **Ingestion Contract** is a versioned adapter contract for one source layer.  It
names the accepted source schema, stable-feature-key policy, selected attributes and
normalisation, CRS transform, partition scheme, spatial predicate, and the exact
relevant code/dependency-manifest fingerprint.  Any change that can change a
normalised row, feature selection or geometry changes this fingerprint; performance
work proven to preserve it does not.

The source's identifier is used only as the source declares it:

- a source feature observation is scoped to one Source Export and layer;
- a publisher feature key becomes a cross-release logical key only when the
  Ingestion Contract records the publisher's stability guarantee; and
- a database FID/row ID, row number, filename and geometric-nearest-match are
  never feature identities.  Without a stable publisher key, the canonical feature
  content is its only reusable key.  Ambiguous identical features without a unique
  source key fail closed rather than acquiring an invented order.

An **Evidence Partition Key** is stable spatial address, not a requested council:
`{source_layer, partition_scheme, cell}`.  Version 1 uses the declared
`bng-10km/v1` cell identifier for Great Britain vector layers which validate a
transform to `EPSG:27700`; the current governed Open Roads configuration and the
compiler's metric spatial work already meet that condition.  A source that cannot
meet it is unsupported in v1 until it has an explicit contract; it does not silently
fall back to arbitrary source tiles.  A feature intersecting several cells is present
in each needed cell and is deduplicated by source feature identity when queried.
This makes a disconnected Evidence Coverage an ordinary set of cells and lets
overlapping Area Definitions reuse them.

Partition content and source provenance deliberately have different fingerprints:

```json
{
  "partition_key": {
    "source_layer": "os-open-roads/RoadLink",
    "partition_scheme": "bng-10km/v1",
    "cell": "ST56"
  },
  "ingestion_contract_fingerprint": "<sha256>",
  "availability": "available | no-data | explicit-unknown",
  "partition_content_fingerprint": "<sha256 of availability + sorted normalised feature content>",
  "source_export_fingerprint": "<sha256 of the raw governed export>",
  "partition_attestation_fingerprint": "<sha256 of export + content + contract>"
}
```

The content fingerprint includes the key, contract, required closed availability
state and the sorted feature-content fingerprints, including cardinality.
`available` requires one or more features; `no-data` and `explicit-unknown` require
zero, so an empty result cannot silently conflate absence with an unresolved fact.
The attestation proves that exact content was obtained from one exact Source Export.
An Evidence Coverage/evidence snapshot is a sorted set of attestations, so it can
replay the selected raw exports.  Version 1 uses the attestation, not cross-export
content equality, as an enrichment dependency: a newer export creates a new
attestation and therefore a new dependent enrichment.  The content fingerprint
remains useful for deterministic validation and later benchmark-led refinement; it
is not a v1 cross-export cache hit.

### Edges and enrichments

A **stable edge ID** names an undirected logical network edge, independently of its
current geometry.  It is a fingerprinted tuple of its edge role, its canonical
endpoint node keys, and the canonical forward-or-reverse-minimum sequence of
constituent source logical keys.  Where a source cannot supply stable keys, its
feature-content fingerprint is the constituent key and the edge is intentionally
not stable across a changed feature.  The record separately binds the current
`satn-network-geometry-v1` fingerprint and the exact source feature observations.
Direction-specific results use the contract's canonical geometry direction (and a
separate `forward`/`reverse` field), never traversal order as an edge ID input.

There is no automatic split/merge aliasing.  A source split, merge, changed endpoint
key or changed constituent sequence creates a new stable edge ID by default; the
old edge and its provenance remain historical.  A future governed reconciliation
may explicitly state a one-to-many or many-to-one lineage map, but geometric overlap
alone cannot carry identity across that change.

Version 1's governed evidence dependency is the sorted set of source-layer
**partition attestation fingerprints** used by the enrichment.  It is deliberately
coarser than a per-query feature read set: a changed source export, ingestion
contract or partition selection creates a new attestation and invalidates only the
enrichments that name that partition.  This is selective across source layers,
spatial cells, edges, algorithms and parameters while keeping the first cache slice
small and auditable.  It does not attempt cross-export equality or exact read-set
reuse; that refinement requires a benchmark and a separate contract decision.

```json
{
  "contract": "satn-edge-enrichment/v1",
  "stable_edge_id": "edge:v1:<full-sha256>",
  "geometry_fingerprint": "<satn-network-geometry-v1 sha256>",
  "partition_attestation_fingerprints": ["<sha256>", "<sha256>"],
  "algorithm": {
    "id": "gradient-profile",
    "contract": "satn-gradient-profile/v1",
    "implementation_dependency_fingerprint": "<sha256>"
  },
  "parameters_fingerprint": "<sha256 of data-only base-unit parameters>",
  "enrichment_fingerprint": "<sha256 of every field above>"
}
```

An **Enrichment Algorithm** and a **Scenario Configuration** are frozen data
contracts, not names or mutable settings.  Their version plus relevant code and
dependency manifest is fingerprinted.  A Scenario Configuration contains only its
Area Definition identity, Criteria Set, Network Selection Profile and other
data-only choices; accepted decisions remain their own fingerprinted input.  The
Scenario Compilation fingerprint binds all of these plus the exact Evidence Coverage
snapshot, accepted decisions and selected compiler dependency manifest.  It excludes
store paths, database bytes, creation time, query plan, RTree state and cache hits.

A **Logical Artifact** is any immutable semantic record such as a partition content
record, partition attestation, Edge Enrichment or Scenario Compilation.  It carries
its contract, full fingerprint and complete dependency/provenance references.  A
database row, table, RTree or exported file is only a materialisation of one or more
Logical Artifacts.

### Invalidation and reuse

| Change | Reuse / invalidation rule |
| --- | --- |
| Add a disconnected or overlapping requested area | Materialise only missing partition keys. Existing partition attestations and enrichments remain valid. |
| New Source Export | Re-ingest the affected/requested source-layer partitions and create new attestations. Invalidate only enrichments naming those attestations; v1 does not compare a new export's rows for a cross-export cache hit. Historical scenarios retain their old export attestation. |
| Ingestion Contract change | Rebuild partitions for that source/layer and invalidate enrichments derived through those attestations; other sources remain valid. |
| Edge geometry fingerprint change | Keep the stable edge ID if its logical key is unchanged, but create a new enrichment and invalidate its downstream artifacts. |
| Source split/merge or edge logical-key change | Create a new edge and enrichment; do not alias or mutate the old one without explicit governed lineage. |
| Algorithm or relevant dependency change | Invalidate only that algorithm-contract's enrichments and their dependants. |
| Parameter change | Create a distinct enrichment; all other parameter sets remain reusable. |
| Scenario configuration or accepted-decision change | Create a new Scenario Compilation; reuse enrichments whose partition attestations, geometry, algorithm and parameters exactly match. |
| Local Evidence Store rebuild, index change or file-byte change | No semantic invalidation. Verify manifests and source checksums; replace or transactionally refresh the cache. |

The Local Evidence Store keeps a union of disconnected partition records. It
validates manifest/source checksums/CRS/indexes before a transactional refresh;
database-format migrations use an atomically replaced sibling file. Its bytes are
never an identity, authority or input to a Scenario Compilation; deleting the store
and rebuilding the same Logical Artifacts is valid.

## Invariants

1. Raw governed Source Exports and their checksums are authoritative; derived
   manifests and database files are reproducible claims that must validate back to
   them.
2. Every dependency is versioned, full-fingerprint-addressed and acyclic.  A
   Scenario Compilation records the exact source attestations named by every reused
   enrichment.
3. Coverage is a set of spatial partition attestations, never a continuous frontier,
   national preload requirement or Area Definition identity.
4. A dependency mismatch fails closed.  Caches may accelerate validation but cannot
   make a missing, stale or geometrically incompatible input acceptable.
5. SHA-256 is deterministic local content identity and staleness detection, not a
   signature, credential or claim of publisher/operator identity (ADR 0007).

## Rejected alternatives

- **Hash the database file or RTree.** Page layout, index build order and engine
  versions are operational; this would make a cache authoritative
  and non-portable.
- **Use one council/Area Definition as a partition.** It cannot reuse an overlap,
  couples disconnected requests, and re-ingests too much after a small change.
- **Use raw WKB, coordinates or geometric overlap as an edge identity.** It repeats
  the portability and traversal problems resolved by ADR 0009 and cannot honestly
  identify a split or merge.
- **Add exact per-query read sets and cross-export equality reuse in v1.** This can
  reduce invalidation further, but would add a second selective-dependency protocol
  before the local-store benchmark proves it is needed.  Version 1 invalidates by
  source-layer partition; a future measured decision may add a new read-set contract.
- **Create a permanent spatial database or central identity registry.** One local
  user needs neither a service nor mutable database authority; content records give
  the required reuse and lineage.

## Migration implications

This introduces new contracts rather than reinterpreting existing cache values.
Existing compiled artifacts and their ADR-0009 network identifiers remain historical
and valid under their recorded contracts.  A future Evidence Refresh implementation
must create fresh Source Export, partition and attestation manifests; it must not
infer them from legacy database bytes, FIDs or cache paths.  Existing
`satn-network-geometry-v1` identities are retained unchanged.  Any later semantic
change to this decision receives a new contract version and causes the affected
derived artifacts to be recomputed rather than silently aliased.
