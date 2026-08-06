# ADR 0019: Compilation retains validated stage and geographic artifacts

- Status: accepted
- Date: 2026-08-05
- Issue: #345
- Related: ADR 0005, ADR 0009, ADR 0010, ADR 0011, ADR 0012, ADR 0013, ADR 0014, ADR 0015, ADR 0016 and ADR 0018

## Context

The compiler already defines immutable evidence identities, a Scenario Iteration DAG,
a Local Evidence Store and atomic publication. Production compilation does not yet
retain or resolve most stage results. Its current publication reuse is effectively
all-or-nothing: a changed review-map asset can cause evidence loading, routing and
network selection to repeat even when the semantic Scenario Compilation identity is
unchanged. A WECA run has consequently taken tens of minutes and is too onerous for
ordinary iterative review.

The same regional compilation also need not be one serial task. Bath, Bristol and
other coherent parts can begin independently, but administrative boundaries do not
provide stable evidence identity and independent route fragments cannot simply be
merged by geometry. Cross-boundary ownership, continuity, obligations, alternatives,
gaps and provenance must remain deterministic.

## Decision

### Automatic retained DAG

Ordinary `satn compile AREA` resolves the ADR-0012 DAG through immutable Retained
Artifacts in the workspace. Incremental operation is the default and requires no
cache flag. Snapshot and Evidence Refresh remain explicit governed operations; the
compiler never performs a hidden download or silently advances an evidence state.

Every Retained Artifact is addressed by the full SHA-256 of a canonical Artifact
Manifest. The manifest binds its kind and contract version, canonical parameters,
stage implementation and dependency fingerprints, sorted upstream artifact
identities, semantic partition and coverage identities, output content digests,
validation contract and deterministic diagnostics. Paths, database bytes,
timestamps, scheduler order, worker count, elapsed time and memory use are excluded.

The resolver visits the graph in stable topological order. A candidate hit is used
only after its manifest, output digests, validation contract and entire named
dependency closure validate. A missing, corrupt or unverifiable artifact is a miss;
it is quarantined rather than repaired in place, and only it and its descendants are
recomputed. A separate run report records hit/miss reasons, timings, worker
observations, stitch state and recovery without becoming authority for reuse.

Presentation receives its own implementation fingerprint. When only presentation
dependencies change, the compiler reconstructs and validates HTML, CSS, JavaScript,
review archives and site artifacts directly from the retained semantic publication
input. It does not load governed source networks, reroute or repeat selection.
Publication remains an atomic validated replacement and a failed attempt leaves the
last valid publication available.

The operational controls are deliberately small:

- `--full` bypasses reuse for a clean-room build and retains the validated result;
- `--rebuild-stage STAGE` forces one stage and its descendants for one invocation
  without deleting historical artifacts;
- `--artifacts PATH` overrides the workspace-local artifact root;
- `--workers auto|N` changes execution only, never semantic identity; and
- `--explain-reuse` emits the complete planned dependency reasons in addition to
  concise normal progress.

### Stable geographic identity, adaptive execution

The semantic Compilation Partition is the versioned EPSG:27700 British National
Grid 10 km cell `bng-10km/v1`, matching evidence identity. Administrative subareas
such as Bath and Bristol are request, output and scheduling hints only. An Execution
Bundle may coalesce adjacent cells for useful work size, memory locality and lower
serialization overhead. Bundle shape, worker allocation and completion order are
non-semantic, so a small grid never requires one process per cell.

Each partitioned stage declares a versioned, fingerprinted read-only halo. A feature
that crosses cells has exactly one owner under the versioned rule: the
lexicographically smallest intersecting core cell. Other cells may retain halo
references but cannot emit a second authoritative copy.

Workers consume compact manifests and retained file-backed inputs. They emit owned
internal fragments, halo references, explicit Boundary Portals, candidate fragments,
diagnostics and gaps. A Boundary Portal is a governed network node or canonical
boundary intersection, never a proximity snap or visual crossing.

Bath, Bristol and other Execution Bundles may start independently. Local compilation
may precompute deterministic internal portal-to-portal and obligation fragments. A
global portal graph then resolves cross-partition obligations and alternatives; the
Deterministic Partition Stitch owns authority-wide selection and invariants. If the
initial halo is insufficient, it requests a targeted extension artifact for the
affected partitions rather than restarting the area.

Stitch input is consumed in canonical sorted order and validates CRS, identifiers,
directionality, ownership, provenance and dependency closure. Compatible duplicates
resolve to their owner. Missing or conflicting optional boundary evidence produces
an explicit Network Gap and Evidence Request. Malformed or unverifiable required
governed input fails closed. One deterministic serial retry is permitted after an
unexpected worker failure; a repeated optional failure becomes an explicit gap.

### Portable local parallelism

The POC supports local parallel execution but does not define a distributed worker
protocol. Coarse CPU-heavy Execution Bundles use a bounded process pool with an
explicit macOS `spawn` context and compact picklable manifests/results. Measured
threads may be used only around native GIL-releasing kernels such as GEOS/Shapely.
DuckDB query parallelism and outer process parallelism are budgeted together rather
than multiplied blindly.

One worker with one DuckDB thread is the deterministic reference. `auto` selects a
locally benchmarked profile subject to exact semantic digest equivalence and a peak
memory budget. M4 settings are not hard-coded into domain configuration. A real M4
benchmark is an implementation acceptance gate because the current restricted
research host could not execute the complete process sweep.

### Retention and provenance

The Local Evidence workspace is the transactional boundary. Artifacts are built in
sibling temporary directories, validated, then made visible atomically. Every
artifact reachable from a publication, Scenario Compilation or active lineage is
retained indefinitely for the POC. Only unreferenced artifacts are eligible for an
explicit, dry-run-first garbage collection after a configurable grace period. There
is no silent size-based eviction. Remote/shared artifact transport is deferred.

The final publication provenance names the complete artifact DAG. A retained
materialisation never replaces governed evidence authority and cannot weaken the
Alignment Resolution Completion Guarantee.

## Migration

Migration is additive and proceeds through independently releasable slices:

1. Separate semantic publication inputs from presentation assets and add the
   presentation-only atomic republisher. This yields immediate UI iteration benefit
   without changing network compilation.
2. Add pure versioned Artifact Manifest/run-report schemas, canonical serialization,
   validation, atomic materialisation, quarantine and retention lifecycle.
3. Adapt real ADR-0012 stages to the resolver behind the existing compile interface.
   Keep the legacy complete path available as the clean-room reference during
   equivalence work.
4. Add partition, halo, ownership, Boundary Portal and targeted-extension contracts
   against synthesized boundary fixtures before changing regional execution.
5. Add the canonical Deterministic Partition Stitch and prove one-bundle versus
   many-bundle equivalence.
6. Add the bounded local executor and benchmark-driven worker profile. Parallelism
   is enabled only after one-versus-many-worker digest equivalence passes.
7. Rebuild B&NES and WECA retained artifacts from governed inputs. Existing snapshots,
   locks and publications are not reverse-engineered into trusted manifests. Cut over
   the default only after all gates pass; rollback selects the previous compiler and
   preserved valid publication, not unchecked partial artifacts.

The dependency-ordered implementation handoff is tracked by
[#347](https://github.com/awjreynolds/agentic-satn-compiler/issues/347):

- [#348](https://github.com/awjreynolds/agentic-satn-compiler/issues/348) — presentation-only republishing;
- [#349](https://github.com/awjreynolds/agentic-satn-compiler/issues/349) — Artifact Manifest store and lifecycle;
- [#350](https://github.com/awjreynolds/agentic-satn-compiler/issues/350) — automatic incremental DAG resolution;
- [#351](https://github.com/awjreynolds/agentic-satn-compiler/issues/351) — geographic partition, halo, ownership and portals;
- [#352](https://github.com/awjreynolds/agentic-satn-compiler/issues/352) — deterministic stitch and targeted extensions;
- [#353](https://github.com/awjreynolds/agentic-satn-compiler/issues/353) — bounded Apple Silicon execution and benchmark; and
- [#354](https://github.com/awjreynolds/agentic-satn-compiler/issues/354) — B&NES/WECA equivalence, cutover and publication.

## Acceptance gates

The implementation must demonstrate:

- a presentation-only change republishes from retained semantic inputs without any
  source loading, routing or selection, targeting less than 60 seconds locally;
- identical incremental and `--full` semantic digests for the synthesized fixture,
  B&NES and WECA;
- one-worker and accepted multi-worker profiles produce identical artifact manifests,
  gaps, selected network and semantic publication digests;
- Bath and Bristol can begin independently and the boundary fixture proves portal
  continuity, deterministic ownership, duplicate handling and a targeted extension;
- changing one partition or stage rebuilds only its dependency descendants;
- corrupt artifacts are rejected and recomputed while the previous publication is
  preserved;
- optional missing work completes with explicit gaps and Evidence Requests, while
  malformed required governed input fails closed;
- the final publication traces every feature through the retained DAG to governed
  evidence; and
- an unrestricted Apple M4 benchmark selects a safe worker/DuckDB profile under the
  declared memory budget and records cold, warm and partial-invalidation timings.

These are correctness gates before speed claims. WECA cold runtime is measured, not
pre-promised; the redesign is accepted only if retained partial and presentation-only
runs materially avoid the currently repeated work.

## Consequences

The design introduces more explicit stage contracts and disk use, but makes
reproducibility, recovery and performance observable. Stable cells maximize reuse;
adaptive bundles avoid nonsensical microtasks. Local processes fit CPU-heavy Python
work on Apple Silicon without making one machine's topology part of network identity.

Directly trusting old outputs, using administrative areas as artifact keys, merging
overlapping geometry, treating a run log as a cache, unbounded thread/process pools,
and distributed execution are rejected.
