# ADR 0020: Retained Compilation is the sole artifact-reuse authority

- Status: accepted — implemented
- Date: 2026-08-06
- Supersedes: ADR 0012
- Related: ADR 0019

## Context

ADR 0012 established the Scenario Iteration stage DAG and ADR 0019 established
retained stage and geographic artifacts, but the implementation needed one
explicit boundary for deciding whether a compilation is incremental, targeted,
full or presentation-only. Without that boundary, a public caller or a pipeline
stage could make a second reuse decision, report a hit without validating it, or
publish a partial materialisation.

## Decision

Retained Compilation is the sole public orchestration authority for artifact-reuse
controls and retained-run reporting. It is a typed seam around the existing
deterministic compiler and publisher; it is not a second network compiler, a cache
API or a workflow continuation. The private pipeline callback remains the current
migration implementation of the reuse mechanics behind that boundary.

### Typed intent and outcome

One invocation enters `compile_retained()` with a frozen
`RetainedCompilationIntent`. The intent validates the requested retained stage
controls, full-build flag, worker setting and reuse-explanation request. A valid
invocation returns a frozen `RetainedCompilationOutcome` containing the ordinary
`CompilationResult`, its validated `CompilationRunReport`, and the persisted
report path. The report is diagnostic provenance and never becomes authority for
the selected network or publication.

The supported targeted stage names are `edge-enrichments`, `routing-assembly`,
`scenario-selection`, `presentation` and `publication`. Retired evidence-input
operations remain explicit `snapshot` or `evidence refresh` commands; they are
not silently converted into retained compile rebuilds.

### Public compile and pipeline ownership

The public `pipeline.compile` signature and result contract remain unchanged.
It normalises the caller's configuration, creates the typed intent and retained
store, and supplies the existing deterministic compiler as an internal callback
to `compile_retained()`. Pipeline code orchestrates inputs, publication authority
and callbacks; it does not expose a competing public reuse decision path. During
migration the private callback still performs the existing retained decoding,
resolution and validation checks in `pipeline.py`. Those mechanics are an
implementation behind the typed boundary, not another API or authority, and may
move incrementally without changing the intent/outcome contract. Future stage
adapters must report their disposition through this seam rather than inventing a
second cache identity.

### Validated dispositions

The seam records one stable ordered report for the complete attempt from the
validated result metadata produced by the private callback. It covers:

| Disposition | Contract |
| --- | --- |
| Whole hit | Existing semantic and publication artifacts are validated before reuse; no unchecked output is served. |
| Presentation-only | A changed fingerprinted presentation asset republishes from validated semantic input without loading evidence or reselecting the network. |
| Stage reuse | Matching retained edge-enrichment, routing/assembly and scenario-stage identities are validated before a dependent stage is skipped. |
| Targeted or full rebuild | The requested stage and its descendants are recomputed without deleting historical artifacts; `--full` remains an explicit clean-room mode. |
| Run report | Every callback attempt that returns a terminal `CompilationResult` writes a typed report with mode, result, ordered artifact events, publication validation/replacement and reuse explanations when requested. An exception raised before a result exists propagates without fabricating a completed report. |

Whole-publication validation and atomic replacement remain mandatory. A failed
presentation or publication attempt leaves the last valid publication available;
missing, stale, corrupt or unverifiable retained inputs are misses, not authority.

## Consequences

- Reuse policy, stage controls and run-report vocabulary have one discoverable
  public owner, while the existing public compile path remains source-compatible.
- Presentation iteration can avoid semantic compilation when retained semantic
  input validates, and targeted rebuilds make their invalidation boundary visible.
- Every hit incurs manifest/output/lineage validation and report-writing work;
  retention therefore improves repeatability and observability, not just speed.
- The retained store and report are operational materialisations. The
  `Effective Strategic Network`, Scenario Compilation and publication validators
  retain semantic and governance authority.

## Rejected alternatives

- Letting each pipeline stage decide its own cache hit would permit conflicting
  identities and make a run report unable to explain the actual reuse path.
- Replacing `pipeline.compile` with a second retained-only public API would split
  callers and make ordinary compilation semantics conditional on the caller.
- Treating a run report, cache path or database bytes as a trusted artifact would
  weaken manifest validation and make storage layout part of semantic identity.
- Serving an unchecked partial materialisation after a failed validation would
  violate atomic publication and the Alignment Resolution Completion Guarantee.

## Implementation status

Implemented on 2026-08-06. `RetainedCompilationIntent`,
`RetainedCompilationOutcome` and `compile_retained()` provide the seam; the
unchanged public `pipeline.compile` delegates orchestration through it. The private
pipeline callback still contains retained decoders and resolvers; migrating those
mechanics behind this module is pending and does not change the single public
authority boundary. Focused implementation coverage validates whole-hit,
presentation-only, stage-reuse, targeted/full-rebuild and run-report paths. The
historical stage-DAG decision in [ADR 0012](0012-scenario-iteration-stage-graph-and-reuse.md)
remains useful context, while
[ADR 0019](0019-retained-incremental-and-geographic-compilation.md) defines the
retained geographic and lifecycle contracts that this seam applies.
