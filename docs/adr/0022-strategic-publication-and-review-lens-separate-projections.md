# ADR 0022: Strategic Publication and Review Lens separate semantic projection from interaction state

- Status: accepted — implemented
- Date: 2026-08-06
- Related: ADR 0019, ADR 0021

## Context

The Effective Strategic Network is a semantic result, while a public map needs
different projections of that result and a browser needs transient interaction
state. Mixing those concerns lets a publisher silently add or omit features,
lets a map hover mutate a selected network, or makes a changed JavaScript asset
look like a changed compilation. The publication boundary therefore needs a
closed, fingerprinted projection and the Review Lens needs a pure state seam.

## Decision

Strategic publication is a pure semantic projection of the stored Effective
Strategic Network and its governed contextual records. Review Lens interaction is
a separate pure state machine plus a browser adapter. Neither layer selects,
repairs or mutates the semantic network.

### Strategic publication projection

`project_strategic_network()` consumes one fingerprinted planning result and
optional governed Places, Asset Accounting, DfT Motor-Traffic Evidence and
diagnostic inputs. It returns a `StrategicNetworkPublicationProjection` whose
outer dataclass is frozen and whose JSON-serializable mapping and sequence fields
are treated as a closed projection view, with:

- the default GeoJSON `Strategic Network` and `Places` feature collections;
- separate named layers for `Strategic Network`, `Places`, `Candidates discarded`,
  `Existing Assets`, `Upgradeable Assets`, `DfT Traffic`, `Graph Diagnostics` and
  `Officer Divergence`;
- a complete strategic reviewable feature roster, including selected sections,
  retained candidates, material gaps and officer/compiler divergence variants
  when supplied by the semantic result; and
- the source `strategic_result_fingerprint` plus a
  `projection_fingerprint`, deterministic default/optional layer rosters and
  the semantic legend.

The projection owns this complete reviewable roster and its fingerprints. It
never calls the selector, treats Places as strategic route geometry, or uses a
presentation choice to decide which semantic record exists. The default GeoJSON
remains intentionally small: Strategic Network plus the separate Places layer.
Optional layers preserve inspectable candidates, governed assets, DfT evidence,
Network Gaps, Material Officer–Compiler Divergences and diagnostics without
changing the selected network.

### Geometry and diagnostic contracts

- Strategic Network line features come only from the stored effective sections;
  a Network Gap is an endpoint marker, never indicative route geometry.
- A gap endpoint is projected to a governed Place Point when that Place exists.
  Null geometry is permitted only when the gap endpoint Place is genuinely absent;
  other feature rows must have valid geometry or be omitted under their source
  contract.
- DfT Motor-Traffic Evidence remains an optional evidence layer and may use the
  bounded candidate-line fallback described by its properties; it never becomes
  a route selection input.
- Graph Diagnostics are data-only records with stable diagnostic identities, not
  geometry-bearing features. Their unknowns and limitations remain explicit.
- Assets, Candidates and Officer Divergence retain source/result fingerprints and
  their distinct semantic layer identities; they are not spliced into the
  default Strategic Network collection.

### Publisher boundary

The publisher owns file I/O, atomic replacement, archive generation and
cross-artifact validation. It consumes the projection and currently checks the
artifact roster, stable feature identities, strategic fingerprint lineage,
allowed WGS84 geometry and nulls, gap completeness, sidecar/reviewable equality
and the absence of legacy strategic-spine features. It records the projection
fingerprint but does not independently recompute that fingerprint during artifact
validation. It does not semantically splice features, reclassify candidates or
invent fallback geometry while writing. A failed validation leaves the previous
valid publication intact.

### Review Lens state and browser adapter

`assets/review-lens-state.js` is a pure UMD module that owns the Review Lens
catalog, stable artifact identities, action reducer and complete view projection.
It stores preview, pin and comparison state without DOM or map references. The
browser `review-map.js` adapter owns only DOM/map effects and compatibility state:
it translates hover, focus, click, close and comparison gestures into reducer
actions, then renders the module's projected view. It cannot call a selector or
maintain a mirrored semantic selection.

The module is a fingerprinted presentation-only asset. A change to the module or
browser adapter may trigger validated presentation-only republishing, but it
does not reload governed evidence, rerun network selection or change the
Effective Strategic Network fingerprint.

## Consequences

- Semantic publication can be tested as a deterministic pure function and
  republished without coupling it to browser event timing or DOM shape.
- The default map stays legible while every selected, candidate, asset, evidence,
  gap, divergence and diagnostic record remains available through named layers.
- Review Lens interactions are reversible and exploratory; Segment Comparison
  cannot become a route-selection input, and unavailable evidence remains Unknown.
- The recorded projection fingerprint and validated presentation dependency
  fingerprint distinguish semantic projection identity from a UI-only change;
  presentation-only retained reports do not claim to recompute the projection.

## Rejected alternatives

- Letting publisher writers append contextual features directly would create a
  second, un-fingerprinted semantic projection and make validation order-sensitive.
- Merging Places, assets or candidates into the default Strategic Network layer
  would hide their distinct roles and imply that contextual evidence was selected
  route geometry.
- Encoding diagnostics as null-geometry map features would turn data-only
  findings into accidental route artifacts.
- Treating every missing geometry as a permitted null would hide malformed
  candidates and evidence; only genuinely absent gap endpoint Places may lack a
  geometry.
- Keeping Review Lens state in the browser adapter or rerunning selection on
  hover would make interaction state a competing authority and make presentation
  changes semantic rebuilds.

## Implementation status

Implemented on 2026-08-06. The pure strategic publication projection and its
recorded fingerprints are consumed by publisher writing and the current
cross-artifact checks described above, while the UMD Review Lens state module and
browser adapter enforce the semantic/interaction split. The presentation asset is
included in the retained presentation dependency fingerprint; the Effective
Strategic Network remains the sole selected state as defined by
[ADR 0021](0021-effective-strategic-network-is-sole-selection-authority.md).
