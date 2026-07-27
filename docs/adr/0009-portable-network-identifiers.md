# ADR 0009: Portable governed network identifiers

## Status

Accepted.

## Context

The same fixed A1/A2 Cross-Spine inputs produced different Strategic Spine,
access, branch, meeting and connector identifiers on macOS and Ubuntu. Strategic
Spine identity hashed raw GEOS WKB produced by `union_all`; byte direction and
component order are not a portable semantic contract. Every downstream rural
Backbone identifier then inherited the changed root ID. Meeting endpoints also
inherited whichever undirected root traversal sorted first.

The generated rural network identity audit found these geometry- or
traversal-sensitive inputs:

- Strategic Spine IDs used raw post-union WKB;
- direct School frontier attachment IDs used raw point WKB;
- fallback routing node and edge-attachment IDs used formatted source
  coordinates, including a possible negative zero;
- access and branch IDs inherited Strategic Spine and attachment identities;
- Branch Meeting IDs and endpoint provenance used traversal orientation; and
- Cross-Spine Connector IDs inherited the Branch Meeting ID and orientation.

Source evidence identifiers, content/file digests, published geometry and
non-Backbone feature families are separate contracts. They are not silently
reinterpreted by this decision.

## Decision

Governed network geometry identity uses `satn-network-geometry-v1`:

- CRS is mandatory and represented by its authority code (for example,
  `EPSG:4326`), falling back to compact WKT2 only where no authority exists;
- coordinates are two-dimensional; Z and M values do not participate;
- X and Y are rounded to nine decimal places;
- rounded zero is always positive zero;
- consecutive coordinates that collapse at that precision are deduplicated;
- a line uses the lexicographically smaller of forward and reverse coordinate
  sequences; and
- MultiLineString members use the same line rule and are then sorted by their
  canonical JSON representation.

Only Point, LineString and MultiLineString are valid network-identity geometry.
Empty, non-finite, precision-collapsed or unsupported geometry fails closed.
Canonical JSON is SHA-256 hashed before it enters the established prefixed
identifier function.

An undirected Branch Meeting endpoint is the tuple
`(root_spine_id, branch_id, place_id)`. The lexicographically smaller endpoint
is always `from`; ID inputs and provenance use that order, and emitted meeting
geometry is reversed when necessary. Lists of source and lineage identifiers
remain sorted.

Strategic Spine provenance records the canonical geometry contract and full
SHA-256. Duplicate canonical Strategic Spine IDs are rejected as collisions;
the existing authoritative publication duplicate checks remain in force.

## Consequences

Identical governed inputs now produce identical Backbone identifiers and
provenance on supported macOS and Ubuntu environments, across repeated
compilations and source-row reordering. A focused macOS CI job checks the same
static exact fixture that the normal Ubuntu suite checks.

This is an identifier migration. Existing compiled deployment artifacts,
decision-ledger references, review links and caches containing old Strategic
Spine, access, branch, meeting or connector IDs must be regenerated together.
There is no alias from old geometry-derived IDs because the old value did not
have a portable meaning. Governed source evidence IDs remain unchanged.
