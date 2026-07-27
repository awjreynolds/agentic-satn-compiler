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
   edges and content binding all agree.  Its anchor Network Place is retained
   solely as typed endpoint/routing identity; it is not a Network Place
   obligation or a served Network Place.  Its only hard and served obligation
   is the admitted Strategic Destination.  A canonical surrogate is used
   solely for `AlignmentCandidateSet` endpoint mechanics; it is never
   published as a Network Place.

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

The next bounded layer compiles both units through the existing separate
population, education, existing-alignment (when supplied), directness,
topography and uncertainty criteria.  A private typed adapter translates the
unit shape into the governed criterion assembler; its legacy connection-shaped
key is never exposed.  Strategic Destination option evidence is derived only
from the admitted destination and its exact current forward/reverse graph
binding.  Interurban candidates retain only their two Network Place
obligations.  The resulting `ScenarioCompilation` therefore classifies the
Railway/A-road alternatives as substitutes and the campus access as a
complementary required role.

This criteria-and-Scenario layer remains inspect-only.  Its bounded review
ledger may request agent analysis, but an agent cannot adopt the result.  Human
adoption and exact Reference application remain separate later authorities.
The high-risk pre-backbone replay seam is intentionally not crossed until it
can regenerate destination access as a first-class role without changing the
ordinary compiler or public backbone signature.

## Consequences

- A governed site/access geometry schema is required before destination access
  can be prepared; a context name or proximity is insufficient.
- This Wayfinding seam can supply finite inputs to a later LCWIP pipeline but
  does not author an LCWIP, route audit, intervention, design, programme or
  delivery decision.
- The output does not claim a route is safe, suitable for independent travel,
  lawful, feasible, cheap, deliverable, funded or adopted.
- Criteria and Scenario selection now cover both role-specific units without a
  weighted aggregate score.
- Reference application, pre-backbone whole-network replay, human adoption and
  publication remain a later slice.
