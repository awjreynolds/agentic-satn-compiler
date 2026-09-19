# Compiler concepts for an agentic planning mid-end

- Issue: [#437 — Identify compiler concepts that fit an agentic planning mid-end](https://github.com/awjreynolds/agentic-satn-compiler/issues/437)
- Retrieved: 2026-09-19
- Status: research note; proposed SATN implications are not an implementation or framework decision
- Context: [CONTEXT.md](../../CONTEXT.md), [ADR 0021](../adr/0021-effective-strategic-network-is-sole-selection-authority.md), [ADR 0022](../adr/0022-strategic-publication-and-review-lens-separate-projections.md)

## Decision aid

The useful compiler analogy is a typed, provenance-carrying planning IR with
explicit admission, analysis, transformation, and publication boundaries. The
front end is evidence admission and lowering into that IR; it is not a web UI.
An intelligent TypeSafe/Jev mid-end could inspect this IR, choose what to
investigate, request compiler-mediated candidate generation, and propose
iterative typed operations. Deterministic validation and action execution would
still control which operations enter authoritative state. The back end would
verify and publish semantic projections. The current authority line is recorded
by [ADR 0021](../adr/0021-effective-strategic-network-is-sole-selection-authority.md)
and [ADR 0022](../adr/0022-strategic-publication-and-review-lens-separate-projections.md);
whether a rebuilt architecture retains or supersedes those boundaries is a
planning decision, not a consequence of compiler terminology.

The table separates established compiler facts from SATN implications:

| Compiler concept (established fact) | Useful SATN mapping (proposed implication) | Boundary or difference |
|---|---|---|
| MLIR represents operations and typed values in nested regions; dialects define extensible operations, attributes, and types, and different abstraction levels can coexist ([MLIR Language Reference](https://mlir.llvm.org/docs/LangRef/), retrieved 2026-09-19). | Define a planning IR vocabulary for admitted evidence, Network Places, candidate sets, alignment sections, decisions, gaps, lineage, and publication projections. Keep evidence and authority as typed fields/operations rather than prompt text. | A planning IR models governed geospatial decisions, not executable machine code. MLIR’s fact does not choose the IR’s planning authority or granularity. |
| Operation definitions can carry operand/result/type constraints and generated or custom verifiers ([MLIR ODS](https://mlir.llvm.org/docs/DefiningDialects/Operations/), retrieved 2026-09-19). | Verify each admitted record and each transformation at stage boundaries: identities, fingerprints, citations, allowed actions, topology, and projection completeness. | Local structural validity does not establish policy acceptance, feasibility, or adoption. Unknown and unresolved evidence are valid domain states where the SATN contract says they are. |
| MLIR dialect conversion supports progressive lowering; partially converted operations may coexist, with type conversion and materialization connecting converted and unconverted values ([MLIR LLVM IR Target](https://mlir.llvm.org/docs/TargetLLVMIR/), retrieved 2026-09-19). | Lower source exports and evidence snapshots in stages: raw governed records → lossless planning graph → selected network/result → publication projection. Retain explicit boundary records while a stage is incomplete. | “Lowering” must not discard source provenance or turn an absent fact into a route. A gap is a semantic result, not an accidental half-lowered operation. |
| Analyses are separate, lazily computed, cached objects; MLIR assumes analyses are invalidated unless a pass proves they are preserved ([MLIR Pass Infrastructure](https://mlir.llvm.org/docs/PassManagement/), retrieved 2026-09-19). LLVM’s pass manager also propagates invalidation through analysis dependencies ([LLVM New Pass Manager](https://llvm.org/docs/NewPassManager.html), retrieved 2026-09-19). | Cache connectivity, candidate eligibility, evidence coverage, and projection checks against the exact IR/dependency fingerprint. When a choice, source snapshot, profile, or rule changes, invalidate dependent analyses and recompute before selection/publication. | A model response is not an analysis cache. Reusing it requires an exact recorded request and dependency match. |
| MLIR rewrite patterns are compiler transformations with a match phase and a rewrite phase; canonicalization is iterative and explicitly best-effort, with convergence safeguards ([MLIR Pattern Rewriter](https://mlir.llvm.org/docs/PatternRewriter/), retrieved 2026-09-19; [MLIR Canonicalization](https://mlir.llvm.org/docs/Canonicalization/), retrieved 2026-09-19). | Use deterministic rewrites for normalization, candidate reduction, gap materialization, and projection shaping. Give agentic planning a typed choice operation whose permitted effects are compiler-authored. | Do not treat “no rewrite matched” as a solved planning question. Do not import a canonicalizer’s implementation iteration limit as a SATN completion policy. |
| MLIR pass managers schedule passes at nested operation levels, and a failed pass can stop the pipeline; pass instrumentation can observe pass/analysis events ([MLIR Pass Infrastructure](https://mlir.llvm.org/docs/PassManagement/), retrieved 2026-09-19). | Make stage order, inputs, outputs, diagnostics, and provenance explicit in a run manifest. Record whether a stage completed, produced a gap, made no progress, or failed validation. Worker order may affect execution time but must not become semantic identity. | Agent calls introduce nondeterminism and latency. The deterministic compiler remains the authority for validation and state mutation. |
| LLVM IR is typed and has a verifier for well-formedness; target triple and data layout communicate target details to later code generation ([LLVM Language Reference](https://llvm.org/docs/LangRef.html), retrieved 2026-09-19). MLIR can lower to LLVM through an LLVM dialect or target-specific dialects ([MLIR LLVM IR Target](https://mlir.llvm.org/docs/TargetLLVMIR/), retrieved 2026-09-19). | Treat publication formats and deployment capabilities as explicit validated targets: semantic GeoJSON/layers, reports, GIS artifacts, and review metadata. Each target adapter consumes the same stored result and records its source/projection fingerprints. | SATN has publication targets, not a machine instruction target. This analogy does not select LLVM or require a general compiler platform. |

## The agentic mid-end boundary

The current `Agent Runtime`, `Agent Decision Request`, `Agent Decision Record`,
and `Authoritative Stage Mutation` definitions in [CONTEXT.md](../../CONTEXT.md)
describe the present SATN constraint set. They are historical design inputs to
reconsider or explicitly supersede, not proof that a rebuilt mid-end must use a
single request, a finite menu fixed at compilation start, or a one-shot model
response. Compiler facts likewise cannot settle who owns planning authority.

A broader mid-end protocol can let a model choose what to investigate, request
compiler-authored candidate generation, and emit successive typed operations.
Each operation or request should carry the current IR/dependency identity and
provenance; deterministic validation decides whether it is admissible, and a
deterministic action executor performs any authoritative state transition. The
protocol can therefore support iterative planning while retaining replayable
inputs, validation results, and publication lineage.

There are two deliberately different paths:

1. **Recorded replay.** A supplied transcript of model observations, investigation
   requests, candidate-generation requests, or typed operations is replayed only
   when its preconditions and dependency identities match the current IR. Replay
   performs no inference and remains inspectable as recorded provenance.
2. **Fresh inference.** The runtime may produce a sequence of typed requests or
   operations. Each response is validated before execution; malformed, stale,
   unsupported, unavailable, or otherwise invalid responses produce the declared
   fallback or unresolved outcome. Fresh inference must not silently overwrite a
   prior result, but it may advance an explicitly iterative planning stage.

This is stricter than ordinary compiler optimization: a rewrite may be
repeatable, whereas inference is an external observation whose identity,
request or operation, response, citations, responder mode, and validation result
must be retained. The pass-like interface is useful for scheduling and
invalidation; the authority and provenance rules are SATN-specific.

## Completion, no progress, and unresolved outcomes

The compiler needs separate statuses for: a valid transformation, a valid
`Network Gap`/explicit unknown, no applicable transformation (no progress), and
invalid required input or failed publication validation. “No progress” is a
diagnostic about the current pass, not evidence that a route is complete. An
unresolved outcome preserves the governed endpoint/evidence identity and the
reason it cannot be resolved; it must not be converted into an empty result,
invented geometry, or favourable assumption.

Mechanical convergence safeguards may be needed in an implementation, but no
numeric cap or retry count is implied by this note. A configured bound already
owned by a SATN contract can produce a recorded fallback or unresolved result;
otherwise the implementation must report the unresolved condition or fail
closed according to the applicable contract. Completion means the SATN stage
contract has been satisfied, including every required selection or explicit
gap, not that an optimizer happened to stop rewriting. This is consistent with
the existing **Alignment Resolution Completion Guarantee** and **Network Gap**
definitions in [CONTEXT.md](../../CONTEXT.md).

## Revisit points

- **Front end:** ADR 0021’s canonical request/state boundary is the current
  admitted planning boundary. Revisit whether its Effective Strategic Network
  schema, status model, and lineage remain the right boundary for a rebuilt IR;
  preserve equivalent authority and provenance only where the new design needs
  them.
- **Mid-end:** ADR 0021’s single strategic-planning call and ADR 0022’s pure
  semantic projection are current implementation constraints. Revisit them in
  light of iterative investigation, candidate generation, and typed operations;
  deterministic validation and action execution remain useful recommendations.
- **Back end:** ADR 0022’s closed publication projection, fingerprints, and
  atomic validation are current target constraints. They can be retained or
  superseded by an equivalent validated publication boundary; browser
  interaction need not define compiler semantics.

TypeSafe/Jev API and execution details are covered by companion research #436.
This note makes the architectural recommendation above without selecting a
framework or requiring an LLVM dependency.
