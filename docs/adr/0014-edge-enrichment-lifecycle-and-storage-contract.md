# ADR 0014: Edge Enrichments are typed, immutable DuckDB materialisations

- Status: accepted
- Date: 2026-07-28
- Issue: #195
- Related: ADR 0003, ADR 0007, ADR 0009, ADR 0010, ADR 0011, ADR 0012 and ADR 0013

## Context

Stage 5 of ADR 0012 needs reusable facts about canonical network edges without
making a Scenario Compilation, a cache file, or the Local Evidence Store
authoritative.  The facts do not have one common value shape: population capture
is a set of Output Areas, education evidence is deliberately option-scoped,
official classification is source-feature overlap, and topography is directional
profile and section data.  A JSON value blob would obscure those differences and
make validation, export and later reuse weaker.

ADR 0011 already defines the stable logical edge ID, its ADR-0009 geometry
fingerprint, canonical fingerprints, and v1 source-layer/BNG partition
attestations.  ADR 0012 makes Edge Enrichments an additive stage of a local,
synchronous DAG.  ADR 0013 selects one disposable DuckDB+Spatial store and a
GeoPackage export, not two query stores.

The existing population and education derivations are important semantic
constraints.  Population Reach is a whole-Output-Area centroid measure over a
dissolved option geometry, not demand or access.  Education Access derives
option-specific obligations, gaps and Unknowns from governed option evidence; it
does not make a route safety or independent-travel guarantee.  This decision
reuses facts only where their composition is proved equivalent; it does not
relabel either assessment as an edge fact.

## Decision

An **Edge Enrichment** is one immutable, dependency-valid result for one
canonical edge revision and one of four first-class enrichment kinds:

- `population-capture`;
- `education-reach-observation`;
- `official-classification-overlap`; and
- `elevation-profile`.

It is a Logical Artifact, not a Scenario-owned statistic or an authority to
select, publish or adopt a network.  DuckDB tables materialise it.  The store
may be deleted and rebuilt; the full fingerprints and governed Source Exports
remain the replay contract.

### One Local Evidence Store, with an internal deep module

ADR 0013's `LocalEvidenceStore` remains the sole external storage seam and its
four-operation interface remains authoritative.  Within it,
`EdgeEnrichmentStore` is an internal deep module.  The Scenario Iteration
coordinator may invoke its logical interface, but no caller configures, opens or
selects a second store.  That internal interface is:

```text
resolve(requests) -> resolved immutable records + typed values + diagnostics
verify(citations) -> verified immutable records
collect(retention policy) -> dry-run or completed orphan-only GC report
```

`resolve` is also the batch lookup and partial-recomputation operation: it
canonicalises, deduplicates and validates all requests; returns validated hits;
and materialises only missing exact keys.  `verify` is used before a Scenario
Compilation or publication consumes a cited result.  `collect` is an explicit,
local maintenance command, never a worker or eviction daemon.  The public Local
Evidence Store interface returns immutable logical records or GeoDataFrames,
never a DuckDB connection, SQL, a table name, a cache path, a background task,
or a partial result.

The implementation has private seams for the four fixed algorithms and their
fixtures.  Adding a fifth kind is a versioned schema/contract decision, not a
runtime plugin registration mechanism.  This gives callers one deep module while
keeping each value family typed and independently testable.

### Identity and request contract

Every request supplies the canonical stable logical edge record, its current
`satn-network-geometry-v1` fingerprint, an explicit kind, a frozen data-only
parameter set, and the selected active ADR-0010 dependency records.  The module
derives the sorted, duplicate-free partition-attestation set required by the
kind's declared spatial predicate.  It must not accept a caller-supplied broad
area name, database row ID, query plan, local path or database checksum as an
identity input.

The enrichment fingerprint is the ADR-0011 SHA-256 of this canonical payload:

```json
{
  "contract": "satn-edge-enrichment/v1",
  "kind": "elevation-profile",
  "value_schema": "satn-edge-elevation-profile/v1",
  "stable_edge_id": "edge:v1:<full-sha256>",
  "geometry_fingerprint": "<satn-network-geometry-v1 sha256>",
  "partition_attestation_fingerprints": ["<sha256>", "<sha256>"],
  "algorithm": {
    "id": "elevation-profile",
    "contract": "satn-elevation-profile/v1",
    "implementation_dependency_fingerprint": "<sha256>"
  },
  "parameters_fingerprint": "<sha256 of canonical data-only parameters>"
}
```

The canonical parameter payload is retained beside its fingerprint for collision
checking.  It uses named integer base units or normalised decimal strings, never
JSON floats: for example `corridor_radius_mm`, `edge_buffer_mm`,
`sample_spacing_mm`, `sustained_window_mm`, and quantised gradient thresholds.
The value-schema version is identity-bearing.  `created_at`, elapsed time,
cache disposition, database bytes and output outcome are diagnostics or values,
not inputs to the fingerprint.

A Scenario citation names at least the Scenario Compilation fingerprint,
enrichment fingerprint, kind, value-schema version, stable edge ID and geometry
fingerprint.  Citing an edge ID alone is insufficient.

### Minimal typed DuckDB layout

The following is a logical schema, not a public SQL interface.  All digest
columns hold full lower-case SHA-256 values; `outcome` is the controlled enum
`available`, `no-data`, or `unknown`.

```sql
edge_enrichment(
  enrichment_fingerprint VARCHAR PRIMARY KEY,
  contract_version VARCHAR,
  kind VARCHAR,
  value_schema_version VARCHAR,
  stable_edge_id VARCHAR,
  geometry_fingerprint VARCHAR,
  algorithm_contract VARCHAR,
  algorithm_dependency_fingerprint VARCHAR,
  parameters_fingerprint VARCHAR,
  outcome VARCHAR,
  value_fingerprint VARCHAR,
  canonical_identity_payload BLOB,
  created_at TIMESTAMPTZ
);

edge_enrichment_partition(
  enrichment_fingerprint VARCHAR,
  partition_attestation_fingerprint VARCHAR,
  PRIMARY KEY (enrichment_fingerprint, partition_attestation_fingerprint)
);

edge_enrichment_parameter_set(
  parameters_fingerprint VARCHAR PRIMARY KEY,
  contract_version VARCHAR,
  canonical_payload BLOB
);

edge_enrichment_diagnostic(
  enrichment_fingerprint VARCHAR,
  diagnostic_code VARCHAR,
  phase VARCHAR,
  count_value BIGINT,
  decimal_value DECIMAL(20,6),
  detail VARCHAR
);

scenario_enrichment_citation(
  scenario_fingerprint VARCHAR,
  enrichment_fingerprint VARCHAR,
  consumption_role VARCHAR,
  stable_edge_id VARCHAR,
  geometry_fingerprint VARCHAR,
  PRIMARY KEY (scenario_fingerprint, enrichment_fingerprint, consumption_role)
);
```

The two `BLOB` columns hold canonical identity/parameter bytes solely to prove
that equal fingerprints mean equal payloads.  They are not a generic value
column.  Values live in the following typed tables, each keyed by
`enrichment_fingerprint`, with its own primary key and `value_status` where a
row can be explicitly Unknown or NoData:

| Kind | Typed value tables | Required value and provenance shape |
| --- | --- | --- |
| Population capture | `edge_population_capture`, `edge_population_capture_limit` | One OA/centroid observation per edge and radius: OA logical key, whole-OA residents, minimum edge distance in mm, capture/borderline decision, and source feature keys. Limits retain a governed current-development omission state; they do not silently add unmeasured residents. |
| Education reach observation | `edge_education_reach_observation`, `edge_education_reach_evidence` | Target kind/ID, phase where applicable, access-point status, edge-to-access observation/distance, evidence IDs, and controlled Unknown reason. It is a source-side observation, never an edge-level `served`, safety, or independent-travel verdict. |
| Official classification | `edge_official_classification_overlap` | Source feature logical key, publisher's raw classification, declared normalisation-contract version, normalised official class, overlap length in mm and source feature provenance. No absence of an overlay becomes an invented road class. |
| Elevation profile | `edge_elevation_profile`, `edge_gradient_section`, `edge_elevation_sample` | Canonical-direction distance/ascent/descent, sustained-gradient statistic/rationale, then ordered section and sample rows with source evidence keys, quality/coverage fields and directional gradient in quantised units. Reverse traversal is derived from canonical direction, never a second edge ID. |

Rows with `no-data` state that a complete, valid source query produced no relevant
observation; rows with `unknown` state why the required source, coverage, geometry
or method input is absent or unusable.  Neither is stored as null pretending to
be a negative finding.  A parent result is `available` only when its kind's
completeness contract is met; otherwise it exposes the corresponding explicit
outcome and diagnostics.

### Family-specific composition rules

1. **Population capture.** The edge result records OA-centroid facts, not an
   option total.  A candidate may reuse them only when its dissolved geometry is
   exactly the union of the cited canonical edges under the same corridor and
   quantisation contract.  It takes the minimum edge distance per OA, deduplicates
   each whole OA once, then applies the candidate's Area Definition and existing
   sensitivity rules.  Any non-edge geometry, incompatible dissolve rule or
   missing coverage falls back to the established option-level Population Reach
   derivation; sums of per-edge residents are prohibited.
2. **Education reach.** Current `OptionEducationEvidence` contains
   option-specific connector continuity, route-quality evidence and Unknowns. It
   is not generally decomposable by edge.  v1 may materialise only governed,
   spatially addressable reach observations; it must preserve an explicit
   `not-edge-decomposable` Unknown where that is all the source supports.  The
   existing Education Access assessment remains the authority for obligations,
   Network Gaps and Independent-Travel Opportunities until an equivalence-tested
   option adapter is separately decided.
3. **Official classification.** Retain the publisher value, feature identity,
   source export and normalisation contract beside the normalised class.  Candidate
   aggregation unions/deduplicates overlap fragments by source feature and
   geometry policy before calculating shares.  It never treats OSM tags, a
   nearest road or an empty source response as official classification.
4. **Elevation and gradient.** The stored profile has the same coverage,
   sampling, sustained-window and uncertainty semantics as the governed
   topography contract.  A profile without two usable end-covering samples, or
   with a disallowed gap, is `unknown`, not flat.  Short sections remain visible
   but are not promoted to a sustained-gradient statistic.  Candidate aggregation
   applies sections in traversal direction and retains every contributing sample
   and source identifier.

### Lifecycle, transaction and collision rules

1. A caller provides a batch.  The module validates edge logical payloads,
   geometry fingerprints, parameter payloads, source partition coverage and
   active dependency manifest before any lookup.
2. It resolves all exact keys in one batched query.  A hit is reusable only after
   rechecking its core row, complete attestation set, typed value fingerprint and
   referenced partition/source manifests.  A stale or incomplete row is a miss;
   it is never repaired in place.
3. The module computes only misses into transaction-local staging tables.  It
   validates typed primary keys, value status, canonical ordering, coverage,
   source evidence, result fingerprint and diagnostics before inserting the core
   row and all children in one DuckDB transaction.  A successful batch is wholly
   visible or absent.  A failed attempt may retain a separate attempt diagnostic,
   but no failed result is resolvable.
4. On a concurrent exact-key insert, the second writer rereads and returns a
   validated equal result; different canonical identity bytes, attestation set,
   value fingerprint or typed rows are a fail-closed collision.  The same checks
   reject an edge ID bound to a different logical-edge payload, a geometry
   fingerprint bound to different canonical geometry bytes, duplicate set members,
   and contradictory typed rows.
5. A Scenario Compilation records citations only after `verify` succeeds.  It
   cites explicit Unknown/NoData results too.  Publication performs its existing
   full validation and atomically exports the Scenario; it does not publish a
   partly materialised batch.

The result is immutable after commit.  “Invalidation” means a current request no
longer has the exact input key required to reuse it; it does not mutate a
historical record or its Scenario citation.

### v1 invalidation and safe collection

| Change | Recompute / non-reuse | Still reusable |
| --- | --- | --- |
| New Source Export, changed ingest contract, or changed BNG cell attestation | Only requests naming the changed source-layer/cell attestation | Other layers/cells, and historical records cited by their old attestations |
| Edge geometry change with the same stable logical edge ID | That edge's geometry-bound results | Other edges and the old geometry revision |
| Edge logical-key split/merge | New edge and all its results; no automatic alias | Historical edge/result lineage |
| Parameter, value schema, algorithm contract or active dependency change | Only that kind/parameter/dependency key | Other kinds and exact old keys |
| Candidate, criteria, profile or decision change | No Edge Enrichment recomputation by itself | All exact edge results; only candidate/scenario work changes |
| DuckDB rebuild, RTree rebuild, path/query-plan change | None after manifest validation | Every logical artifact rebuilt identically |

V1 deliberately invalidates at the named source-layer/BNG partition attestation.
It does not compare a newer export for equal content and does not record exact
feature read sets.  A change elsewhere in the same named cell can therefore
recompute an affected result; that conservative cost is accepted until a benchmark
justifies a new contract.

GC is conservative and explicit.  It first validates citation and manifest
integrity, takes the store's exclusive writer transaction, and reports candidates.
It may delete an enrichment and all of its typed child/diagnostic rows only when
it is uncited by every retained Scenario Compilation, publication or active local
build pin, is older than the declared retention window, and its governed source
artifacts remain available for rematerialisation.  It never deletes Source Exports,
partition attestations, Scenario citations, published artifacts or history solely
because their cache rows are old.  Deletion is child-first and core-last in one
transaction, with an audit of deleted fingerprints.  The default is dry-run.

### Provenance and export

Scenario provenance retains the sorted exact citation set and each cited
enrichment's fingerprinted input manifest.  The GeoPackage export contains typed
spatial/non-spatial enrichment tables and `scenario_enrichment_citation`, using
the same edge ID, geometry fingerprint, outcome and source references.  It may
also render a concise human-readable citation, but does not flatten unlike values
into a feature property or issue a second query-store export.  The raw governed
Source Export, its licence/release/effective-date declaration and source feature
identity remain the authority behind every derived row.

### Migration and independently testable slices

1. Implement pure canonical request, parameter, result-manifest and citation
   validators.  Test ordering, base-unit encoding, collisions, version changes,
   explicit Unknown/NoData and no dependence on paths, clocks or database bytes.
2. Add the core and typed DuckDB tables with staging/commit validation.  Test
   batch hits/misses, rollback, concurrent equal/conflicting inserts, partition
   set checks and one-cell partial recomputation.
3. Materialise elevation profiles and official-classification overlap beside the
   current source path.  Fixture tests must prove the same directional profile,
   sections, source lineage, classification fragments and Unknowns before any
   compiler consumer changes.
4. Materialise population capture primitives and prove exact option equivalence
   only for the declared union-of-canonical-edges cases, including whole-OA
   deduplication, borderlines and current-development limitations.  Retain the
   current option-level calculation for every other case.
5. Materialise education reach observations with typed `not-edge-decomposable`
   results.  Do not replace the current option-scoped Education Access assessment
   until an explicit, end-to-end equivalence contract covers its gaps and Unknowns.
6. Add Scenario citations and GeoPackage typed export, then let the ADR-0012
   coordinator resolve these sidecar records through the Local Evidence Store.
   Change neither the current snapshot loader nor publication semantics until all
   equivalence tests pass.  GC is deliberately outside the initial implementation;
   when introduced it starts as the explicit dry-run-only, orphan-reporting command
   specified above.

## Consequences

The first implementation has one small caller interface, preserves source
authority and exposes inspectable typed facts.  It enables selective reuse across
scenario changes without a graph database, generic plugin framework, exact
read-set protocol, background worker, or dual GeoPackage cache.  It also makes
the intentional non-decomposability of current education evidence visible rather
than turning a cache optimisation into an unsupported access claim.
