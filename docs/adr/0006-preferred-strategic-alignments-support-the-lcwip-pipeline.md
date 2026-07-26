# ADR 0006: Preferred Strategic Alignments support, but do not author, an LCWIP

- Status: accepted
- Date: 2026-07-25
- Decision owners: SATN product
- Related: ADR 0001, ADR 0002, ADR 0003, GitHub issues #88, #128 and #137

## Context

An LCWIP normally needs a network plan, a programme of improvements and an
explanation of its evidence and method. The SATN compiler already addresses a
smaller, earlier question: which strategic places and access obligations should
form a coherent cycling network?

Several plausible alignments can remain between the same places. Publishing all
of them with equal prominence creates noise and leaves the most consequential
route choice to routing mechanics or an undocumented judgement. It is useful for
the compiler to reduce that finite candidate set to the best-evidenced
**Preferred Strategic Alignment** under a declared **Network Selection Profile**.

That reduction is still not an LCWIP. Population near a corridor is not demand;
an existing asset is not necessarily reusable; a strategic line is not a design;
and a preferred alignment is not a funded, feasible, consulted or adopted
scheme.

## Decision

The SATN compiler's **Wayfinding Pass** is an upstream component of an LCWIP
pipeline. For each governed strategic connection it may:

1. generate a finite, reproducible set of plausible **Alignment Options**;
2. retain population, education, existing-alignment, directness, gradient,
   validity and uncertainty as separate evidence sections;
3. distinguish substitute options from complementary network roles;
4. apply a frozen, versioned, data-only **Network Selection Profile**;
5. emit bounded, replayable agent decisions only for configured material
   ambiguity; and
6. publish one inspectable **Scenario Compilation** that may become a
   **Reference SATN** through a governed human selection.

Compiler behaviour remains deterministic from governed inputs, configuration and
accepted decision records. Configuration declares policy choices such as
candidate source precedence, but cannot waive topology, continuity, mandatory
School Access Obligations or other hard safeguards. Candidate precedence is a
bounded presumption among otherwise eligible, near-equivalent substitutes, not
an unconditional winner.

Different declared profiles may generate comparable Scenario Compilations. The
compiler does not silently choose which scenario becomes authoritative.

The current compiler integration is deliberately narrower than this complete
decision. **Spine Access Candidate Preparation** generates finite routing
alternatives, rejects topologically invalid inputs before admission and retains
an exhaustive disposition roster. **Scenario Compilation** may promote only a
Community-kind row whose declared parent is another Spine Access Connection and
whose distinct child and parent Community identifiers exactly match every
candidate's endpoints and served Network Places. A direct Strategic Spine
attachment remains explicitly out of scope; an unresolved row, a candidate set
with no options or one whose options were all rejected cannot disappear or
become publishable by default.

Criterion lineage is fail-closed. Population source identity must equal the
prepared population source; an option-specific education assessment must be a
self-validating deterministic extension of the exact prepared register and
destination source snapshot; network evidence is bound to the complete canonical
prepared connection; and topography is bound to the candidate-set identity,
candidate geometry and declared gradient. These content hashes identify and
replay inputs only: they are not signatures, credentials, certificates or trust
roots, and the open-source compiler requires no repository secret.

This integration emits an immutable, reviewable Scenario Compilation and
replayable review requests. It does not create a Reference SATN, mutate the
compiled network, invoke an agent provider or exercise publication authority.

The following remain separate downstream LCWIP capabilities:

- demand analysis, including commute, school-trip or mode-shift modelling;
- walking and wheeling network planning;
- route audit, intervention specification and detailed design;
- legal, land, condition, cost and feasibility conclusions not supported by
  separately governed evidence;
- programme prioritisation, funding allocation and phasing;
- consultation, equality assessment, political adoption and monitoring; and
- long-form LCWIP report authoring.

LCWIP-facing products may consume compiler artifacts to create a concise public
overview, route cards, technical evidence, consultation records, a delivery
programme and committee exports. They must not reinterpret a Preferred Strategic
Alignment as a safe, feasible, funded or adopted scheme.

## Rationale

The boundary gives planners a useful stake in the ground while preserving honest
claims. It also allows a regional compilation to reduce parallel-route noise
programmatically before expensive scheme investigation begins.

A configuration contract supports different legitimate council policies without
forking compiler logic. Separate evidence sections prevent a composite score from
hiding a missed school, uncertain current development, declassified route status
or another material conflict. Bounded agent review is reserved for ambiguity that
the compiler can describe as a finite decision; an agent cannot invent geometry,
evidence, weights or policy.

## Consequences

- The default review map can show one Reference SATN and reveal rejected
  alternatives progressively.
- Every selection must retain its evidence, rejected candidates, uncertainty,
  profile fingerprint and decision provenance.
- Changed profiles or accepted decisions create a fresh Scenario Compilation;
  they do not mutate an earlier result.
- Candidate-generation gaps remain non-publishable and review-required until an
  explicit governed gap decision is recorded.
- Version 1 uses whole-network replay after a changed decision ledger. Targeted
  spatial regeneration is deferred until dependency-closure equivalence can be
  proven.
- Existing NCN, Greenway and similar assets can receive a bounded advantage only
  from the evidence level actually supplied. Status alone cannot imply legal
  access, condition, low cost or feasibility.
- Schools remain mandatory network obligations. Secondary and all-through
  secondary phases may additionally receive an Independent-Travel Opportunity
  view, but that evidence cannot be described as a safe or accessible route
  without the required authority.
- Population Reach v1 reports whole 2021 Output Area population whose
  population-weighted centroid lies in a 500 m or 1 km straight-line corridor.
  It is not labelled a five-minute walk, demand or population actually connected.
- A later LCWIP support tool should consume the governed outputs rather than
  expanding `satn.compile()` into a monolithic planning system.

## Alternatives rejected

### Publish every plausible route without reduction

This preserves evidence but recreates the noise and indecision the feature is
intended to remove.

### Embed one fixed national route-selection policy

Legitimate local strategies differ. Hard-coding a single order would turn policy
into undocumented compiler behaviour and make scenario comparison difficult.

### Use one weighted score

A total can conceal mandatory obligations and conflicting evidence. Separate
criteria and an explicit hierarchy are more inspectable.

### Make the compiler generate a complete LCWIP

This would mix network wayfinding with demand, feasibility, scheme development,
consultation and delivery authority. The resulting claims would exceed the
compiler's governed evidence.

### Let an agent choose a route freely

Free-form geometry or policy would not be reproducible. Agents therefore select
only compiler-authored finite actions against fingerprinted evidence.
