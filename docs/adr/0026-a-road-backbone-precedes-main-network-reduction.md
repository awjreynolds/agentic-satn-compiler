# A-road backbone precedes main-network reduction

- Status: accepted
- Date: 2026-09-05
- Supersedes: ADR 0024's smallest coverage-preserving mesh as the primary selection objective

The owner clarified that A roads establish the strategic network's backbone.
Cycle routes and Greenways are often preferable delivery options, but choosing
them must preserve the strategic connections that the A-road network provides.
The compiler must therefore begin with the urban and rural A-road connections,
retain those obligations through selection, and publish either a selected
alignment for each connection or an explicit unresolved gap. Alternative
alignments may replace A-road sections; a nearby route alone does not establish
that a connection has been replaced. The backbone's loops must not disappear
merely because a connected tree passes a proximity calculation.

This corrects a loss of intent between earlier backbone assembly, candidate
selection, and later mesh reduction. Tickets document that evolving exploration;
their closure is not acceptance evidence. The existing proximity calculation
measures coverage of its candidate line inventory, not the usefulness or
interconnection of a regional travel network. It can inform optional coverage
routes but cannot delete the backbone's required connections or establish a
claim of globally smallest, Dutch-style mesh conformance. The Effective
Strategic Network remains the sole selection authority, with separate Access
Support and publication that projects the actual selected result. A-road
alignments remain strategic infrastructure proposals, not assertions of present
cycling suitability or detailed scheme design.

An official A-road connection retains its exact proposal geometry even when an
endpoint cannot attach to nearby OSM linework. OSM attachments enable route
alternatives and access connections; they do not determine whether the supplied
A-road backbone exists. Disconnected official components remain a separate
continuity question and must not be joined by invented geometry.

Administrative boundaries can cut a continuous source road into disconnected
pieces. Retain evidenced cross-boundary continuations within the configured
source context, and show their selected geometry in the map and PDF. An exact
official junction link between A-road components is structural context even
when its road classification differs; preserve that classification and the
proposal's intervention status. Wiltshire's source demonstrates why this
matters: treating two junction links as absent suggested an unnecessary
17.5 km A-road detour.

Connections that require choosing another regional corridor remain explicit
review questions. Do not add an arbitrary tree of out-of-area roads solely to
make the component count one. Candidate search failures remain diagnostics;
published Network Gaps describe unresolved obligations after effective
selection, so a failed alternative cannot turn a satisfied connection into a
gap.

An isolated component made solely of unattached classified unnumbered urban
roads is context, not a mandatory strategic connection. Classification alone
does not establish that such a fragment serves the main network. Retain its
source geometry and explain its exclusion without stopping the regional build
or adding a strategic gap obligation for it. Connected classified unnumbered
sections retain their existing role. Required A-road and B-road sections still
retain exact proposal geometry with a located gap when attachment is unresolved;
no synthetic connector is implied.
