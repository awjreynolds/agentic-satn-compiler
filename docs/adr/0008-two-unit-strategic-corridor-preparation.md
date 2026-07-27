# ADR 0008: Prepare strategic corridors as two sibling logical units

- Status: accepted
- Date: 2026-07-27
- Decision owners: SATN product
- Related: ADR 0001, ADR 0002, ADR 0006 and GitHub issue #137

## Context

Spine Access preparation deliberately retains a direct Community-to-Strategic-
Spine attachment as out of scope.  That row is a one-place attachment, not a
two-place strategic alignment, and turning it into one would misrepresent its
parent role and access-connection identity.

The Bath–Saltford proving case instead exposes two related, non-substitutable
questions: a parallel interurban Railway/NCN versus A-road-related corridor
between the exact direct-spine Community anchors; and a separately admitted
Strategic Education Destination's connection from a current network anchor to
its governed site access point.

## Decision

The compiler prepares two sibling units:

1. `INTERURBAN_SPINE` uses only compiler-emitted direct-spine Community anchors
   and finite current RoadGraph options between their exact nodes.  It compares
   alternatives with the same role and endpoint obligations.
2. `STRATEGIC_DESTINATION_ACCESS` exists only for a current, explicit governed
   admission whose record/version, `site_id`, access-point evidence identifiers,
   current site geometry, explicit graph node, exact incident forward/reverse
   edges and content binding all agree.  It has one anchor Network Place and
   only the admitted destination as its Strategic Destination obligation.  A
   canonical surrogate is used solely for `AlignmentCandidateSet` endpoint
   mechanics; it is never published as a Network Place.

Both units retain forward and reverse graph edges, canonical geometry, source
and evidence identifiers, deterministic content identities and a physical-
alignment registry.  Exact duplicate route outputs are collapsed before
admission while all generating strategies remain provenance.  The registry
owns and emits each canonical authoritative geometry once while retaining all
logical-role and candidate memberships.  Missing, offset or mismatched
destination geometry becomes an explicit typed preparation issue; no service
conclusion is inferred.

The sibling module does not change `SpineAccessCandidatePreparationResult`,
does not promote its direct-spine rows, and has no selection, agent, Reference
replay, geometry mutation or publication authority.  A private aggregate
preparation view can present both unit families to later criteria/scenario work
without renaming a strategic unit as an `access_connection_id`.

## Consequences

- A governed site/access geometry schema is required before destination access
  can be prepared; a context name or proximity is insufficient.
- This Wayfinding seam can supply finite inputs to a later LCWIP pipeline but
  does not author an LCWIP, route audit, intervention, design, programme or
  delivery decision.
- The output does not claim a route is safe, suitable for independent travel,
  lawful, feasible, cheap, deliverable, funded or adopted.
- Reference replay and publication remain a later slice, after exact selection
  and lineage/application bindings are implemented for both unit types.
