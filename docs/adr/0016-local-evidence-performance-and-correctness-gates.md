# ADR 0016: Local evidence performance gates preserve network semantics

- Status: accepted
- Date: 2026-07-28
- Issue: #194
- Related: ADR 0003, ADR 0005 and ADR 0009–0013

## Context

The Local Evidence Store and Scenario Iteration exist to remove repeated work, not
to change evidence quality, network meaning or publication safeguards. Large,
machine-sensitive timings are unsuitable as ordinary pull-request tests, while a
benchmark with no semantic oracle can make a wrong result look fast.

Current evidence provides:

- a completed B&NES cold compile and validated publication at 87.31 seconds;
- B&NES identical-input publication reuse at 4.70 seconds;
- a canonical B&NES published-network fingerprint,
  `bc1d356a8809c0dd916e201f897fc8bedf1ff6a2431c5c645fe75878bc94e315`;
- a historical WECA core compilation at 1,757.57 seconds which fails the new
  600-second target, plus an older incomplete publication run which is not a
  completion baseline; and
- exact result equivalence between the DuckDB and GeoPackage benchmark for every
  tested layer/window.

Evidence Refresh and changed-configuration Scenario Iteration do not yet have
completed baselines. Unknown measurements remain unknown rather than being
inferred from generated artifacts or progress heartbeats.

## Decision

Use one versioned acceptance manifest per benchmark run. It binds the commit,
machine, Python/runtime versions, DuckDB/Spatial versions, Area Definition,
Source Export and snapshot checksums, store-state fingerprint, scenario/decision
fingerprints, command, cold/warm definition, exit status, wall time, peak RSS,
stage diagnostics, result counts and semantic fingerprints. A timing without this
manifest cannot pass a gate.

Correctness is always a hard prerequisite. Performance results are considered only
after all semantic, provenance and atomic-publication checks pass.

Strategic-corridor routing records deterministic phase dimensions alongside a
separate elapsed-time observation: direct-spine anchors, governed directed pairs,
single-source route searches, unique alignments and display sections.  Timing is
diagnostic only and never contributes to a candidate, selection, preparation
fingerprint or semantic oracle.  The compact synthetic routing benchmark exercises
10, 25 and 50 anchors, records each elapsed time and proves that finite pair routing
is batched by role, strategic-use and start node.  It retains the exact geometry,
edge identities, candidate ordering and tie behaviour of individual routing; it does
not impose a semantic pair limit or introduce a server/runtime architecture.
Each recorded run uses the same manifest discipline as the wider performance gates;
the compact acceptance evidence is retained in
`docs/benchmarks/strategic-corridor-routing-2026-08-01.json`.  Fields that do not
exist for a synthetic in-memory RoadGraph (Area Definition, Source Export, snapshot,
store, decision and DuckDB/Spatial bindings) are explicit Not Applicable values,
never omitted or fabricated.

## Acceptance corpus

| Corpus | Purpose | Required oracle |
| --- | --- | --- |
| Small synthetic/disconnected-area fixture | Fast tests for partition identity, overlap reuse, Oxfordshire-without-intervening-coverage behavior, invalidation and failure paths. | Exact rows, partition attestations, fingerprints, Explicit Unknown/NoData and transaction result. |
| B&NES | Small real semantic and cold-performance fixture. | Existing connection/gap/map-feature counts and canonical published-network fingerprint; store-backed and snapshot-backed results must match. |
| A4017/Overndale Road | Authoritative-source precedence fixture. | Governed classification supersedes conflicting OSM context while disagreement evidence remains inspectable. |
| WECA v10 | Scale, disconnected partition/query, Edge Enrichment, routing, publication and memory fixture. | Pinned Source Export/snapshot checksums, exact stable feature/network identities, successful atomic publication and complete run manifest. An incomplete historical run is never the oracle. |

An intentional semantic migration updates an oracle only through a reviewed ADR and
a before/after explanation. Regenerating expected hashes because a test failed is
not acceptance evidence.

## Gate matrix

| Gate | Measurement | Pass condition |
| --- | --- | --- |
| Store result equivalence | Reference governed-source query versus DuckDB exact predicate, for stable IDs, canonical geometry and declared attributes. | Byte-identical canonical result fingerprint and identical Explicit Unknown/NoData status. |
| Spatial subset | Fresh process connection plus repeated same-process queries for the small urban and whole-council windows; report p50 and worst. | Worst measured query <=2 seconds on the reference machine. WECA-wide full-layer extraction is reported separately as bulk I/O. |
| Evidence Refresh correctness | New, overlapping and disconnected coverage; changed Source Export and ingestion contract; forced mid-refresh failure. | Only required partitions change; previous validated state survives failure; manifests, CRS and RTree validate. |
| B&NES cold | No reusable Scenario materialisations or prior publication; governed store already provisioned. | Successful atomic publication <=120 seconds and exact semantic oracle. |
| Scenario Iteration | Change Criteria Set, Network Selection Profile or accepted decisions while stages 1–6 retain matching fingerprints. | Fresh validated Scenario Compilation and atomic publication <=60 seconds; reuse diagnostics prove no Evidence Refresh or routing/assembly recomputation. |
| WECA cold | Pinned v10 corpus; no reusable scenario materialisations; store coverage already refreshed. | Successful complete atomic publication <=600 seconds with exact oracle. |
| Identical-input reuse | Unchanged inputs and validated prior publication. | Existing full-publication validation remains mandatory; record 4.70 seconds as baseline, not as Scenario Iteration evidence. |
| Offline provisioning | Empty isolated environment supplied only the pinned wheel and matching cached Spatial extension. | Store verification/query succeeds with network disabled; missing/mismatched extension fails with actionable diagnostics. |

Evidence Refresh timing is recorded separately from cold Scenario Compilation.
Downloading a Source Export is never part of either gate. Source
read/normalisation, DuckDB import/index/validation and transaction commit are
separate measurements.

## Measurement rules

1. Use the declared reference Mac and pinned inputs/dependencies. Record power mode
   and whether other material workloads were active.
2. A cold Scenario run removes only disposable scenario materialisations in a
   dedicated benchmark directory. It never deletes the live store or publication.
3. A fresh-process query is not described as a cold OS-cache query unless the page
   cache was actually controlled. Report connection/process reopen and page-cache
   state separately.
4. Run partial queries three fresh-process and five same-process times; report every
   sample, p50 and worst. Stop rather than endlessly repeat a pathological case.
5. One complete WECA cold run is sufficient during development because it is
   expensive; three successful runs are required before a release claim uses p50.
   B&NES and Scenario Iteration use three runs for a release claim.
6. Peak RSS and database/output sizes are mandatory observations. Until a governed
   hard memory budget exists, a >20% increase against the last accepted corpus is a
   review trigger, not an invented failure threshold.
7. A >15% wall-time increase against the last accepted p50 is a review trigger even
   when the absolute budget still passes. Absolute budgets remain the release gate.
8. Failed, interrupted or non-atomically published runs are retained as diagnostics
   and always fail; their last heartbeat is not elapsed time.

## CI and local responsibility

Ordinary pull-request CI runs the synthetic fixture, canonical identity tests,
DuckDB schema/transaction/query tests, extension-version check, A4017 precedence
fixture, B&NES semantic-equivalence slice where practical, and all publication
validators. It does not download national evidence or enforce noisy laptop wall
times.

The reference Mac runs B&NES timing, changed-configuration Scenario Iteration,
DuckDB partial-query timing and the offline provisioning test before integration.
WECA cold and bulk-extraction gates run for a release candidate or when routing,
assembly, evidence, DuckDB schema or publication dependencies change. Their signed
off local manifests are attached to the implementation ticket; generated spatial
artifacts remain outside Git.

CI verifies the schema and checksums of committed compact benchmark manifests. It
must not treat an author-written `passed: true` field as evidence: pass/fail is
recomputed from bound measurements, semantic hashes and the budgets above.

## Cutover rule

The existing snapshot-backed path remains the authority comparator until:

1. DuckDB refresh/query tests pass for disconnected and overlapping coverage;
2. B&NES and A4017 store-backed results match their semantic oracles;
3. a complete WECA store-backed run passes correctness and records its timing;
4. changed-configuration Scenario Iteration proves stages 1–6 were reused and
   completes within 60 seconds; and
5. the existing whole-publication validator accepts the fresh atomic output.

Missing a performance budget does not permit weaker validation or silent evidence
loss. It creates a measured optimization ticket with the failing stage diagnostics.

## Consequences

Fast unit and semantic tests remain suitable for every change, while large local
measurements are reproducible and reviewable instead of becoming flaky CI. The
architecture cannot claim success from file size, a partial heartbeat or unchanged
input reuse. Quality and provenance remain fixed gates; optimization is allowed only
inside those contracts.
