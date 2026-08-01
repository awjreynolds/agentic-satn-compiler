# ADR 0006: Preferred Strategic Alignments support, but do not author, an LCWIP

- Status: accepted
- Date: 2026-07-25
- Last amended: 2026-08-01
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
6. complete every valid-input candidate set through a validated agent choice,
   declared deterministic fallback or explicit Network Gap; and
7. publish one inspectable **Generated Scenario Compilation**, or an
   **Officer-Informed Scenario Compilation** when pre-loaded Officer Decisions
   apply.

Compiler behaviour remains deterministic from governed inputs, configuration and
accepted decision records. Configuration declares policy choices such as
candidate source precedence, but cannot waive topology, continuity, mandatory
School Access Obligations or other hard safeguards. Candidate precedence is a
bounded presumption among otherwise eligible, near-equivalent substitutes, not
an unconditional winner.

Different declared profiles may generate comparable Scenario Compilations. An agent
choice or deterministic fallback grants generated provenance only. Officer Decisions
are initial governed inputs, apply only to their stable logical targets and never wait
for interactive approval during generation. The compiler continues to evaluate its
evidence-preferred option; a different applied Officer Decision creates a prominently
displayed **Material Officer–Compiler Divergence**, with the compiler-preferred route
distinguished from ordinary muted alternatives.

The current compiler integration is deliberately narrower than this complete
decision. **Spine Access Candidate Preparation** generates finite routing
alternatives only for chained Community-to-Community Spine Access connections,
rejects topologically invalid inputs before admission and retains an exhaustive
disposition roster. A direct Strategic Spine attachment is retained in that
roster as explicitly out of scope and is not silently analysed as a two-place
alternative. **Scenario Compilation** may promote only a Community-kind row
whose declared parent is another Spine Access Connection and whose distinct
child and parent Community identifiers exactly match every candidate's endpoints
and served Network Places. An unresolved row, a candidate set with no options or
one whose options were all rejected cannot disappear or become publishable by
default.

Criterion lineage is fail-closed. Population source identity must equal the
prepared population source; an option-specific education assessment must be a
self-validating deterministic extension of the exact prepared register and
destination source snapshot; network evidence is bound to the complete canonical
prepared connection; and topography is bound to the candidate-set identity,
candidate geometry and declared gradient. Every criterion packet also captures
the exact preparation identity, raw lineage and complete evidence-fingerprint
set from which it was derived, so criteria cannot be replayed against a
re-fingerprinted preparation with changed raw artifacts. Each per-candidate-set
snapshot contains exactly one binding for every required assessment kind. These
content hashes identify and replay inputs only: they are not signatures,
credentials, certificates or trust roots, and the open-source compiler requires
no repository secret.

This integration emits an immutable, reviewable Scenario Compilation and replayable
decision evidence. It does not create a Reference SATN, mutate the compiled network or
exercise officer or adoption authority. The current narrower implementation may not
yet exercise the Agent Runtime and completion fallback described by this decision.

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

## Verification contract

Parallel-route reduction is accepted against a governed synthetic proving corpus,
not a live or named real-world area. The corpus has two levels:

1. A light acceptance suite runs one complete composite compilation on every pull
   request. Its named synthetic zones are separated beyond the maximum configured
   rural parallel-candidate distance so no route can interact accidentally with a
   different example.
2. A deep suite runs when parallel-reduction compiler or configuration contracts
   change, before release and through an explicit manual trigger. It tests values
   below, exactly at and above decision thresholds and only those multi-factor
   interactions capable of changing an outcome; it does not enumerate every
   combination of unrelated settings.

The composite acceptance compilation contains zones proving:

- brief convergence is rejected while a sufficiently parallel divergence/rejoin pair
  forms one candidate set;
- the same separation can fail urban discovery, pass rural discovery and remain
  explicitly bracketed when local scope is unresolved;
- two base alignments can generate exactly one materially evidence-justified,
  continuous hybrid transition while both bases remain retained alternatives;
- a materially dominant option resolves without an Agent Runtime;
- near-equivalent options resolve through the declared deterministic hierarchy;
- conflicting material evidence resolves through one valid scripted agent choice;
- the same kind of conflict completes through deterministic fallback after a
  scripted runtime failure;
- an Access-Only Quiet Lane remains eligible and is preferred to an otherwise
  comparable unfiltered through-traffic village street;
- a visual intersection without a Junction Node creates only a Crossing Warning,
  while a required missing bridge creates a Network Gap with a bridge Intervention
  Archetype; and
- a pre-loaded Officer Decision remains selected while a conflicting current compiler
  preference is retained as a highlighted Material Officer–Compiler Divergence.

The light discovery examples use deliberately clear values: 20 percent symmetric
coverage fails, 90 percent passes, and an 800 metre separation distinguishes the
configured 500 metre urban and 1,500 metre rural profiles. The deep suite covers
79/80/81 percent symmetric coverage, 499/500/501 metre urban distance and
1,499/1,500/1,501 metre rural distance, together with reversed input ordering,
complete versus missing evidence, valid and invalid runtime response classes and
repeat-run identity.

Every scenario or zone is a checked-in data-only governed manifest declaring metric
geometry, evidence, all active configurable values, profile identity and deterministic
scripted-runtime behaviour. No live AI model participates. Each complete compilation
is compared with a checked-in canonical expected-result artifact containing the exact
closed roster of candidate sets, selected and retained alignments, decision modes,
fallback triggers, Network Gaps and divergences. Environment-dependent paths, timings,
timestamps, usage, model identity and generated prose are excluded. Any unexpected
addition, omission or change fails even if the eventual selected route is unchanged.

The light fixture may have a non-authoritative visual reference for human orientation,
but review-map generation, publication and screenshot comparison are outside this
verification contract. Expected results are read-only in CI. Updating them requires an
explicit regeneration command and review of the semantic diff before the fixture and
new result are committed together.

## Consequences

- The default review map can show one Reference SATN and reveal rejected
  alternatives progressively.
- Every selection must retain its evidence, rejected candidates, uncertainty,
  profile fingerprint and decision provenance.
- Changed profiles or accepted decisions create a fresh Scenario Compilation;
  they do not mutate an earlier result.
- Candidate-generation and access gaps remain explicit, prominent Network Gaps in a
  completed Reviewable Network; they do not become hidden route assumptions or an
  interactive compilation stop.
- Applied Officer Decisions remain the selected routes. A conflicting current
  compiler preference is highlighted as a Material Officer–Compiler Divergence rather
  than overriding the officer route or appearing as an ordinary grey rejection.
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
