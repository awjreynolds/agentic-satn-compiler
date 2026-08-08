# ADR 0012: Scenario Iteration is a small DAG of immutable materialisations

- Status: superseded by ADR 0020
- Date: 2026-07-28
- Issue: #192
- Related: ADR 0003, ADR 0005, ADR 0006, ADR 0008, ADR 0009, ADR 0010 and ADR 0011

> Historical note: ADR 0020 supersedes this ADR's retained-reuse authority model.
> The stage DAG and its contracts remain historical context; current retained
> compilation authority and implementation status are defined by ADR 0020.

## Decision

A Scenario Compilation is produced through the following explicit DAG. Each arrow
passes immutable logical artifacts, never a mutable cache or a live source.

    Source Export validation
            |
    Evidence Refresh: source-layer + BNG partition attestations
            |
    Area extraction
            |
    Canonical hierarchy and network normalisation ----+
            |                                        |
            +--> reusable Edge Enrichments <---------+
                            |
    Routing and network assembly
            |
    scenario criteria, selection and accepted decisions
            |
    whole-publication validation and atomic publication

The sole new external seam is a Scenario Iteration coordinator: given an immutable
Scenario Configuration and accepted-decision ledger, it resolves or validates the
named materialisations, compiles a new Scenario Compilation and asks the existing
publisher to validate and atomically publish it. It returns the Scenario Compilation,
a dependency manifest and per-stage diagnostics. Its interface deliberately does not
expose cache paths, database handles, task queues or partial continuations. Storage,
spatial indexes and per-stage adapters remain implementation details behind that seam.

This is a DAG evaluator, not a general workflow engine, daemon or plugin framework.
It runs locally and synchronously; a materialisation is an optimisation and is never
authority.

## Stage contracts

All fingerprints below are full SHA-256 digests of versioned canonical payloads.
They include the relevant selected compiler dependency-manifest records required by
ADR 0010. They exclude wall clock, absolute paths, local-store bytes, index state,
query plans and cache-hit state. Network geometry and identifiers retain ADR 0009;
evidence partitions, source exports and enrichment identity retain ADR 0011.

| Stage | Inputs to outputs | Fingerprint and materialisation | Invalidate / reuse | Required diagnostics and budget |
| --- | --- | --- | --- | --- |
| 1. Source Export validation | Governed raw export, source declaration, licence, CRS and ingestion contract to validated Source Export or fail-closed error. | Source Export fingerprint; immutable received bytes remain authoritative. A manifest may be retained, not the export identity replaced by a cache. | Changed raw bytes, declared release/effective date, schema/CRS or ingestion contract creates a new export. Identical validated export is reusable. | Source checksum, schema/CRS, licence, feature-key policy and failure reason. No stage allocation yet; record elapsed time as part of cold evidence. |
| 2. Evidence Refresh | Validated exports and requested coverage to BNG/source-layer partition content records and attestations, including NoData/Explicit Unknown rows. | Partition key plus content, contract and Source Export attestation fingerprints. The ADR-0013 DuckDB store is a disposable materialisation updated through validated transactions. | A changed export or contract rematerialises only its requested source-layer/BNG cells and invalidates dependants naming their old attestations. Adding a disconnected area only adds missing cells. v1 has no cross-export content hit or exact read-set reuse. | Requested/present/missing cells, source/feature counts, CRS/index validation, NoData/unknown counts, cache state and elapsed time. <=2 s is only the separately measured partial spatial-subset query gate, not an assumed refresh time or full-layer extraction budget. |
| 3. Area extraction | Area Definition boundary and validated partition attestations to exact in-area evidence views and coverage report. | Area extraction fingerprint: Area Definition identity, ordered coverage attestation set, spatial predicate/working-CRS contract and relevant dependencies. Materialise by this fingerprint. | Boundary, predicate, source-layer coverage or attestation change invalidates this area view only. Other areas and source layers remain reusable. | Selected/deduplicated/rejected feature IDs, cells consulted, coverage/unknown report, exact-predicate counts and elapsed time. No separate allocation; measure within the regional cold budget. |
| 4. Canonical hierarchy and network normalisation | Area view, configured place/hierarchy rules and source-layer facts to canonical Network Places, hierarchy, normalised edges and canonical geometry/identity registry. | Canonical-network fingerprint binds inputs, normalisation contract, ADR 0009 geometry fingerprints and active dependency manifest. Materialise immutable records, not mutable graph objects. | A changed area view, hierarchy/normalisation rule, source logical key or canonical geometry invalidates affected canonical artifact lineage. A stable logical edge with changed geometry receives new dependent enrichment. | Place/edge/cardinality changes, collision/split/merge failures, canonicalisation failures, geometry/CRS checks and elapsed time. No invented time allocation; this static work must be reusable for iteration. |
| 5. Reusable Edge Enrichments | Canonical edge and its geometry plus sorted partition attestation set, enrichment algorithm/version, parameters and relevant dependencies to one Edge Enrichment. | ADR 0011 enrichment fingerprint. Persist only validated immutable enrichment records; cache lookup revalidates every dependency. | New geometry, named partition attestation, algorithm/dependency or parameter produces a new enrichment. Unrelated cells, algorithms and parameter sets remain reusable. | Edge IDs, geometry, named partitions, algorithm/parameter fingerprints, Explicit Unknown/NoData evidence and per-edge/aggregate elapsed time. No allocation yet; stable enrichments are mandatory reuse for changed scenarios. |
| 6. Routing and network assembly | Canonical network, reusable enrichments, static routing/assembly configuration and topology safeguards to finite candidate routes, Backbone-and-Access Network assembly, gaps and deterministic diagnostics. | Assembly fingerprint binds all inputs, route tie-breaking/algorithm and dependency manifest. Materialise candidates and assembly result separately from a scenario choice. | Any routing rule, canonical network, required enrichment, topology safeguard or dependency change invalidates assembly. Criteria, selection profile and accepted decisions do not reroute an unchanged candidate set. | Search/settled-node/relaxation/frontier counters, route/candidate/gap counts, topology and invariant results, enrichment coverage, elapsed time and peak RSS. The recorded WECA no-publication core compile is about 1,757.6 s: evidence that <=600 s is not yet demonstrated, not a time to apportion. |
| 7. Scenario criteria, selection and accepted decisions | Assembly candidates/gaps, Evidence Coverage, Criteria Set, Network Selection Profile and accepted-decision ledger to immutable Scenario Compilation or typed review/gap result. | Scenario fingerprint binds Area Definition, evidence snapshot/attestations named by reused enrichments, criteria/profile, accepted decisions, preparation/assembly identities and selected dependency manifest. | A profile, criteria or decision change creates a new Scenario Compilation while reusing stages 1-6 when their fingerprints match. A stale decision or incomplete/unknown mandatory evidence fails closed; it never changes an earlier scenario. | Candidate-set dispositions, criterion evidence, selected/rejected alternatives, ledger freshness/consumption, Explicit Unknown and Network Gap records, reuse decisions and elapsed time. The complete changed-configuration Scenario Iteration target is <=60 s; no unmeasured stage share is assumed. |
| 8. Publication | Fresh Scenario Compilation, publication configuration and required governed records to one validated immutable publication bundle. | Publication fingerprint/manifest retains current complete dependency, feature, decision and artifact validation. Output directory is a materialisation only. | Any publication input/configuration, scenario, artifact/template dependency or validator change rebuilds publication. An identical validated bundle may take current whole-publication reuse. | Artifact inventory/digests, cross-artifact identity/geometry checks, visible unknowns, validation result, atomic-replace outcome and elapsed time. The observed 4.70 s B&NES value is only identical-input, validated whole-publication reuse; it is not evidence for a changed configuration. |

The Scenario Iteration target therefore means a changed configuration must reuse
validated stages 1-6 and recompute only scenario-specific selection plus a fresh,
fully validated atomic publication. The current 4.70 s reuse path is preserved as a
faster, distinct identical-input path; it validates the existing whole publication
before reuse and does not establish changed-configuration performance.

For a regional cold run, validated source partitions, area extracts, canonical
network records and Edge Enrichments must be materialised/reused whenever their
inputs are already available. This removes avoidable repeated source, geometry and
evidence work, but cannot by itself claim the <=600 s result: the measured WECA core
compile is already about 1,757.6 s. A completed cold run must demonstrate a measured
reduction in routing/network assembly as well as publish atomically before the budget
can pass. No timing is inferred from this DAG.

## Invalidation and observable contracts

| Change | Earliest invalidated stage | What may remain reusable |
| --- | --- | --- |
| New governed Source Export or ingestion contract | 1-2, for its source layer/cells | Other source layers, untouched cells, historical scenarios and their attestations. |
| Add/remove area coverage or change boundary/extraction predicate | 2 for missing cells, otherwise 3 | Existing partition attestations; unrelated area views and enrichments. |
| Source feature split/merge or canonical geometry/hierarchy change | 4 | Unaffected source partitions; an old logical edge remains historical, never aliases automatically. |
| Enrichment algorithm, parameters or active dependency record | 5, for that algorithm/parameter set | Other enrichment algorithms/parameters and upstream artifacts. |
| Routing/assembly rule or active routing dependency | 6 | Valid upstream evidence, normalised network and enrichments. |
| Criteria Set, Network Selection Profile or accepted decisions | 7 | Stages 1-6 exactly when their fingerprints match. |
| Publication-only configuration or artifact implementation dependency | 8 | The Scenario Compilation and all prior stages. |
| DuckDB/RTree rebuild, cache eviction, query-plan or path change | none after validation | Rebuild the same logical artifacts; database bytes never enter scenario identity. |

Every stage record must expose its contract/version, full input and output
fingerprints, complete upstream references, active dependency-manifest selection,
cache disposition (miss, validated-hit or recomputed), diagnostics and a
machine-readable failure/unknown state. A materialisation is accepted only after its
manifest and referenced artifacts validate. Its build occurs in a sibling temporary
directory and becomes visible only by atomic replacement. Historical snapshots,
Scenario Compilations and publication releases are never edited.

Source-layer partition dependencies are intentionally the v1 reuse granularity.
Exact feature read sets and cross-export content equality are deferred until a
benchmark shows their added contract complexity is justified.

## Migration and independently testable slices

1. Add versioned, pure stage-record schemas, canonical fingerprints and manifest
   validators for Source Exports, partition attestations, area views, canonical
   network records, enrichments and assemblies. Prove stable serialisation,
   dependency completeness, rejection of stale/missing inputs and preservation of
   Explicit Unknown/NoData.

2. Materialise and atomically replace local partition stores from governed exports.
   Test disconnected/overlapping coverage, one-cell invalidation, source/contract
   changes, offline rebuild, spatial-index capability and exact-query results. The
   storage benchmark remains the proof point for the <=2 s subset gate.

3. Add sidecar area, canonical-network and enrichment materialisations without
   changing the existing snapshot-loading path. For every fixture, compare the
   current and materialised routes' canonical network identity, source lineage,
   unknowns, gaps, diagnostics and published semantics. The existing snapshot loader
   is CRITICAL-risk and is not changed until these result-equivalence tests exist.

4. Materialise routing candidates and assembly results behind the coordinator; prove
   route tie-breaking, topology, identifiers, gaps and diagnostics equivalent to the
   current path. Run a complete WECA cold benchmark with stage timings, peak RSS and
   atomic publication; do not claim <=600 s before it passes.

5. Enable profile/criteria/decision-only Scenario Iteration through the small
   coordinator. Prove that it reuses stages 1-6, rejects stale ledgers, retains
   immutable earlier scenarios and still invokes the current full-publication
   validation and atomic writer. Record the first changed-configuration timing before
   claiming <=60 s.

Legacy snapshots, deployment locks, publications and cache files keep their recorded
contracts. They are not reverse-engineered into v1 stage records. Cutover creates
fresh materialisations alongside them, runs equivalence tests and a full
publish/validation, then permits the coordinator only for matching v1 artifacts.
Failure falls back to the existing complete compilation; it never serves a partial
or unchecked materialisation.
