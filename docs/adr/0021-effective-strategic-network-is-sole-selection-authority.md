# ADR 0021: Effective Strategic Network is the sole selection authority

- Status: accepted — implemented
- Date: 2026-08-06
- Related: ADR 0006, ADR 0020, ADR 0022

## Context

The compiler has several useful but non-authoritative views of a route decision:
Candidate Sets and their finite candidates, corridor preparation rosters,
officer choices, rejected alternatives, gaps and publication layers. If any
consumer treats one of those views as a fresh selection input, the same request
can acquire different selected geometry or a second fingerprint. The domain needs
one named state between selection and publication.

## Decision

`Effective Strategic Network` is the sole selection authority for one exact
governed compilation request. The strategic planner selects once and stores its
complete result; every later consumer reads that result or a semantic projection
of it.

### Canonical request

`EffectiveStrategicNetworkRequest` is the typed boundary for selection inputs. It
binds the routable network or lossless planning snapshot, corridor preparation,
Area Definition fingerprint, source-snapshot fingerprint and any attributable
officer decisions. Its validation requires a complete governed identity before
selection is allowed. A missing or mismatched identity produces an explicit
unavailable state rather than a guessed network.

### Canonical state and status

`EffectiveStrategicNetworkState` is immutable and contains exactly one of:

- `evaluated`, with the validated strategic planning result and its exact
  fingerprint; or
- `unavailable`, with a structured reason and a fingerprint of that unavailable
  state.

The evaluated result stores the `Effective Strategic Network`, selections,
Candidate Sets, retained alternatives, Network Gaps, Material Officer–Compiler
Divergences, Evidence Requests, diagnostics and planning lineage together. The
state's fingerprint is derived from the stored result; it is not recomputed from
whatever a downstream adapter happens to display.

### Adapter and compiler boundary

The effective-network adapter may translate a routable frame into the lossless
planning graph, derive preparation-facing bindings and preserve compatibility for
a caller that has already run the authority planner. It may not select a route,
repair a gap, invent geometry or replace a stored result. The strategic compiler
is invoked exactly once for a complete request. The canonical compile path
produces the evaluated result; the compatibility constructor trusts the caller's
already-validated planner result and exists only to avoid a second invocation.

### Downstream consumption

The compiler exposes the canonical state and its exact stored result. During
migration, pipeline and publisher code read that same result through the
`strategic_network_planning` compatibility field, while the Review Lens consumes
its serialized publication projection. Those paths may project selected sections,
Places, candidates, assets, DfT Motor-Traffic Evidence, Network Gaps, divergences
and data-only diagnostics, but they never call a selector or run a competing
tie-break. A changed selection requires a fresh governed request and therefore a
new fingerprinted effective state.

## Consequences

- Selection identity, status and lineage are inspectable before any publication
  or browser interaction exists.
- An unavailable governed input is explicit and safely reviewable; it cannot be
  confused with an empty or successfully selected network.
- Compatibility with existing planner callers is retained without making those
  callers authorities, while the exact planner result remains the source for all
  later projections.
- Candidate and preparation evidence remains complete for inspection without
  allowing a review layer to mutate the selected network.

## Rejected alternatives

- Re-running selection in the publisher or Review Lens would make presentation a
  second authority and could produce a different result for the same request.
- Treating the preparation roster or Candidate Set as the selected network would
  conflate finite alternatives with the one effective result.
- Storing only a selected geometry without status, gaps, divergences and lineage
  would lose the explicit unknowns and provenance required by a Reviewable
  Network.
- Returning `None`, an empty GeoJSON collection or a guessed fallback for an
  incomplete request would hide a governed identity failure.

## Implementation status

Implemented on 2026-08-06 in the typed effective-network request/state boundary
and the single-call strategic planning path. Downstream compatibility properties
delegate to the stored result; they do not invoke planning again. The semantic
publication projection and browser Review Lens are governed separately by
[ADR 0022](0022-strategic-publication-and-review-lens-separate-projections.md).
