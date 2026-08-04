# SATN planning routines and material capability gaps

- Date: 4 August 2026
- Code snapshot: `origin/main` at `80aefa1`
- Scope: source-level trace of the current compiler from governed evidence to the
  published strategic map; this is research for issue 316, not implementation.

## Executive finding

The compiler has substantially stronger **selection, provenance, officer-decision,
and display contracts** than its published BaNES network currently exercises. The
principal failure is not a missing colour or a weak final comparator. It is a broken
planning chain:

1. the authoritative Backbone and Strategic Spines are built first;
2. later code generates a small set of alternative paths for only part of that
   network;
3. BaNES runs the legacy selection profile, not the newer reuse-first contract;
4. the newer contract cannot yet be fed by the production candidate producers;
5. selections are projected as review overlays and deliberately do not alter the
   already-compiled network; and
6. prepared interurban Strategic Corridor candidates are not consumed by the public
   scenario-selection seam at all.

This explains how the code can contain tests for “prefer an existing cycleway to a
shorter A-road” while the BaNES strategic map still presents the A/main-road skeleton.
The tested vNext comparator is real, but it is not the end-to-end production path.

## Current planning chain

| Stage | Current source routine | Actual behaviour | Consequence |
|---|---|---|---|
| Routable evidence | [`RoadGraph.__init__`](../../src/satn/routing.py#L119) | Builds one deterministic directed graph from the OSMnx `bike` graph. It retains only `length`, `highway`, `ref`, `oneway`, `satn_alongside`, and `satn_ncn` as routing facts, and keeps only the shortest parallel edge for a `(u,v)` pair. | Rich infrastructure/access tags are unavailable to path costing. Parallel facilities between the same nodes can collapse before candidate discovery. |
| Attachment topology | [`RoadGraph.__init__`](../../src/satn/routing.py#L224) | Attachment search uses reciprocal edges only. If the largest strong component holds at least 90% of attachment nodes, all smaller components are removed from the spatial attachment node index. | A fringe asset can remain in the routing graph yet be unavailable as the nearest community/site attachment. The 90% rule is embedded, not profiled, and produces no officer-facing exclusion record. |
| Strategic seeds | [`_strategic_spines`](../../src/satn/compiler.py#L1870) | Promotes **rural** A-road evidence plus rural NCN, declassified-NCN, and greenway evidence directly into Strategic Spines. | These inputs are treated as initial network geometry, not competing evidence. Urban portions are split out by scope before this stage. |
| Urban skeleton | [`derive_urban_structure`](../../src/satn/urban.py#L67) | Builds official A/B/classified-road spines and treats qualifying cycle routes as circulation boundaries around low-traffic areas, not as strategic route lines. | Through urban areas the visible skeleton naturally favours official main roads even when an existing cycleway is the plausible strategic alignment. |
| Backbone assembly | [`assemble_backbone_outward`](../../src/satn/backbone.py#L625) and [`_candidate`](../../src/satn/backbone.py#L1596) | Grows concurrently from Strategic Spines, always accepting the currently cheapest cycling-network attachment to any served frontier. Rank is distance first; population, demand, existing-asset reuse and network resilience are absent. | This is a deterministic nearest-frontier forest, not a demand-led coherent-network optimiser. It is useful for service coverage, but it determines the ordinary published geometry before Preferred Strategic Alignment selection exists. |
| Candidate endpoint discovery | [`_strategic_route_pairs`](../../src/satn/strategic_corridors.py#L581) | Forms every pairwise combination of direct-spine community anchors within each root spine, plus each strategic destination to its nearest anchor. | This is a complete pair set derived from the already-built Backbone, not from OD flows, corridor demand, hierarchy, centrality, severance, or an explicit finite planning objective. Candidate count grows quadratically per root. |
| Candidate path discovery | [`choose_alignment`](../../src/satn/routing.py#L1392), [`RoadGraph.option`](../../src/satn/routing.py#L1087), and [`_weight_for`](../../src/satn/routing.py#L1461) | Produces at most four distinct shortest paths: direct, A-road-biased, NCN-biased, and low-traffic. Chained community access may add a B-road-biased path. | A later selector cannot choose a route absent from these role-specific shortest paths. This is the immediate discovery bottleneck for cycleways, PROWs, useful off-road assets, and mixed reusable-asset compositions. |
| Candidate classification | [`strategic_corridors._source_class`](../../src/satn/strategic_corridors.py#L1235) and [`spine_access_candidate_preparation._candidate_source_class`](../../src/satn/spine_access_candidate_preparation.py#L1133) | Strategic candidates are “verified existing” only when `ncn_share > 0`. Spine-access candidates additionally require the **route role** to be `ncn-informed` before matching current asset evidence can make them verified existing. | An ordinary cycleway/greenway reached by the low-traffic search can be 100% existing infrastructure and still be `other-routable`. Declassified NCN is not in the current-asset set used at the spine-access seam. PROW is not represented as its own reuse class here. |
| Candidate admission | [`admit_candidate_set`](../../src/satn/alignment_selection.py#L2878) | Preserves every generated candidate and admits a bounded, source/class-diverse subset with deterministic geometry equivalence. | This is strong once candidate facts exist, but it cannot restore collapsed graph edges, missing path alternatives, or misclassified assets. |
| Preferred selection | [`select_preferred_alignment`](../../src/satn/alignment_selection.py#L3740) | Applies hard gates and either the legacy population/source/directness hierarchy or the configurable vNext reuse-first lexicographic hierarchy. | The selection mechanism is not the primary missing algorithm. Its active contract and inputs are. |
| Parallel alternatives | [`compile_parallel_reduction_scenario`](../../src/satn/parallel_reduction.py#L1482) | Groups already-supplied same-endpoint routes by symmetric proximity, splits at explicit choice points, and creates at most a configured number of material hybrids. | This capable routine is reachable from tests/corpus CLI, not from `compile_network`; it does not discover routes from the road graph or improve the published BaNES network. |
| Public application | [`compile_network`](../../src/satn/compiler.py#L639) and [`_compile_reviewable_network`](../../src/satn/compiler.py#L1014) | Candidate preparation happens after Backbone geometry is complete. The reviewable adapter says explicitly that it never alters a compiled frame. It compiles only the Spine Access preparation. | Selected alternatives are review projections, not a rebuilt SATN. `strategic_corridor_preparation` is stored but is not passed into `compile_reviewable_network` or `compile_prepared_scenario`. |
| Review map | [`_reviewable_map_collection`](../../src/satn/publisher.py#L1728) | Shows selected/unselected Spine Access candidates, officer divergences, and separately projects the original compiler-selected Strategic Spines. | The UI can truthfully show alternatives and discarded candidates, but the strategic spine layer remains the upstream A-road/NCN/greenway seed geometry rather than the result of interurban route selection. |

## Exact causal trace for the A-road/cycleway symptom

### 1. Hard-coded path roles define what is thinkable

`_weight_for` discounts A-road edges to 35% of length and penalises other edges to
160% for the `strategic-spine` route. It discounts NCN edges to 40%, but it has no
equivalent role for:

- any existing cycleway independent of NCN status;
- declassified NCN as upgradeable off-carriageway provision;
- PROW/bridleway/footway opportunity with legal/intervention state kept separate;
- a chain mixing several existing asset types;
- low-traffic road plus asset connectors;
- high-demand links or severance avoidance; or
- a bounded set of materially distinct alternatives such as Yen k-shortest paths.

`choose_alignment` then embeds 80% A-road share, 150% A-road detour, and 135% NCN or
quiet detour thresholds, including the policy statement “A-road Strategic Spine
selected for directness and social oversight.” These values are outside the frozen
`NetworkSelectionProfile`. Even though strategic candidate preparation ignores the
early winner and retains the four generated options, the weights still decide the
entire candidate universe.

### 2. Existing-asset evidence is discovered too late and used conditionally

Spine Access preparation computes `current_asset_share` only **after** each route has
been generated. It then assigns `VERIFIED_EXISTING_ASSET` only where the generating
role was `ncn-informed`. Route evidence and route-generation strategy are therefore
conflated: identical existing provision can receive a weaker class merely because it
was found by the low-traffic or direct weight function.

Strategic Corridor classification is narrower still: any NCN share is existing, while
all non-NCN asset evidence is ignored by `_source_class` despite the unused `context`
argument. That is a direct, local explanation for “the obvious cycleway was not the
preferred route.”

### 3. BaNES does not activate the vNext reuse-first policy

[`deployments/banes/area.yaml`](../../deployments/banes/area.yaml#L47) declares the
legacy profile with `candidate_source_precedence`:

1. verified existing asset;
2. A-road corridor;
3. B-road corridor; and
4. other routable.

It does not declare `contract: satn-network-selection-profile/vNext`. Under the legacy
derivation, the primary objective is population reach; source precedence is applied
only to the remaining population contenders. Existing provision is therefore not the
first comparison in all cases.

This is not fixed by changing the YAML alone. Production candidate producers create
legacy `AlignmentCandidateInput` values without `reuse_class`, `intervention_state`,
`alignment_bases`, `primary_alignment_basis`, transitions, fragmentation, or governed
evidence IDs. [`AlignmentCandidateSet.bind_set`](../../src/satn/alignment_selection.py#L1536)
correctly rejects such incomplete inputs under vNext. The vNext tests construct these
facts directly; the public preparation adapters do not yet derive them.

### 4. Strategic Corridor selection is not in the public application path

`prepare_strategic_corridors` produces interurban and strategic-destination candidate
sets, but the public reviewable compiler receives only
`compiled.spine_access_candidate_preparation`. Consequently:

- the interurban candidate sets are not selected by `compile_prepared_scenario`;
- they have no effective officer/compiler selection record;
- they do not become selected/unselected review-map candidate features; and
- they cannot replace or challenge the original Strategic Spine geometry.

This is the most material integration gap. It makes the new Strategic Corridor
preparation an evidence artifact rather than an active planning pipeline.

## Capabilities that are already good and should be retained

- **Deterministic graph traversal:** batched Dijkstra traces explicitly count work and
  replay tie behaviour rather than allowing execution order to determine route IDs.
- **Completion semantics:** optional review/evidence failure produces an incomplete or
  terminal reviewable artifact while the ordinary compilation remains available.
- **Bounded candidate admission:** all generated candidates receive an admission
  disposition; material duplicates and over-limit options are not silently erased.
- **Separate evidence dimensions:** population, education, directness, gradient,
  topography, traffic, existing-alignment and uncertainty are represented independently.
- **Direction-independent effort:** the vNext comparator supports total absolute
  elevation change and treats missing values as unknown, not zero.
- **Officer decisions:** exact candidate IDs can override compiler preference without
  expiry; divergence is retained as a separate finding.
- **Discarded-candidate display:** rejected alternatives are available as a dedicated
  review-map layer and existing/upgradeable alternatives can retain typed display state.
- **Parallel section/hybrid model:** explicit choice points and bounded hybrid creation
  are a sound downstream composition primitive once real candidate routes reach it.

## Material gaps, ordered by impact

### P0 — connect discovery, selection, and authoritative network application

Create one end-to-end compilation contract covering Spine Access, Interurban Spine,
and Strategic Destination Access. It must select every prepared Candidate Set, apply
the effective officer/compiler choice to a new immutable network projection, and keep
the original/generated/rejected geometries in provenance. This is an application
stage, not in-place mutation of the old Backbone.

Without this package, better candidate algorithms will still not change the strategic
map.

### P0 — productionise reusable-asset facts and activate one configurable profile

Derive the full vNext facts for every production candidate from governed evidence.
Classification must be independent of the path-search role. At minimum, distinguish:

- existing cycle provision;
- upgradeable off-carriageway asset, including separately governed PROW/footpath/
  bridleway and declassified-route evidence;
- low-traffic non-A-road;
- major-road protected-infrastructure proposal; and
- unknown/conflicting evidence.

The Area Definition must own all ordering, thresholds, detour rules and unknown policy.
The compiler must reject an internally inconsistent profile but must still emit a
reviewable gap/fallback artifact rather than fail the whole network generation.

### P0 — replace four role paths with configurable finite candidate discovery

Introduce a `CandidateDiscoveryProfile` before admission. It should generate a bounded
set using evidence-aware edge facts and materially distinct path search, not select a
winner. Exact algorithm options should be informed by the NPT/NPW/external research,
but the required output is already clear:

- stable candidate and path-profile fingerprints;
- explicit edge eligibility and cost components;
- separate asset, demand, directness, effort, traffic and constraint observations;
- disconnected/unmatched/excluded records;
- no largest-component or dangle deletion; and
- deterministic limits with every suppressed alternative recorded.

### P1 — demand-led endpoint and corridor seeding

Replace “every pair of already-built anchors” as the only interurban objective with a
profiled finite set of corridor obligations derived from governed Network Places,
strategic destinations, cross-boundary gateways, demand/flow evidence where present,
and explicit coverage/hierarchy rules. All-pairs may remain a diagnostic sensitivity,
not the default definition of strategic need.

### P1 — graph integrity and severance diagnostics

Before routing, emit component, dangle, bridge/cut, reciprocal-access, circuity and
reachability observations. Never silently hide the smaller attachment components. A
policy may exclude them from a specific search, but the exclusion and affected assets/
places must be reviewable.

### P1 — linework matching and extensive/intensive aggregation

Current 20 m buffers and route-role checks are too weak for binding demand, traffic,
asset and PROW evidence to route sections. Adopt a provider-neutral matching result
with source/target IDs, distance, angle, shared length, match state and provenance;
apply extensive and intensive aggregation laws separately.

### P2 — bounded external accessibility and route challenges

Offline, pinned OSMnx/NetworkX graph diagnostics and selected R5/Valhalla/FMM analyses
can add evidence where they answer a declared officer question. They must remain
Governed External Analysis Runs and must never author canonical geometry or silently
become route policy.

### P2 — AI evidence triage only after deterministic packages

AI can explain material conflicts, cite the exact input evidence, request one of a
finite set of missing investigations, and summarise why an officer choice diverges.
It must not create candidates, geometry, thresholds, traffic/safety/legal facts, or a
winner. Invalid, uncited, timed-out, or unoffered responses use the configured
deterministic fallback and compilation still completes.

## Smallest implementation-proving corpus

One checked-in synthetic area can exercise the complete repaired chain:

1. two Network Places and one school/hospital obligation;
2. a direct A-road path with high traffic and no protected-space evidence;
3. a slightly longer existing non-NCN cycleway;
4. a declassified-NCN/PROW path requiring upgrade;
5. a quiet-road path;
6. one fringe component with a useful asset;
7. one deliberate severance and one explicit choice point; and
8. an officer decision choosing a non-compiler route.

Required assertions:

- input permutation does not change IDs, ordering, geometry hashes, or the selected
  result;
- all four materially distinct routes enter discovery with correct evidence-derived
  classes independent of generating strategy;
- the configured reuse-first profile selects the existing cycleway unless an explicit
  detour/constraint rule displaces it;
- the selected route appears in the authoritative strategic projection;
- original Backbone geometry and every unselected/rejected route remain inspectable;
- the PROW/declassified route is shown as upgrade-required, not existing provision;
- the A-road route remains a coloured proposed-major-infrastructure alternative;
- the fringe component is diagnosed, not silently omitted;
- officer choice is applied and compiler divergence is highlighted without expiry;
- missing demand, elevation, traffic, asset, or runtime evidence produces typed
  unknowns/evidence requests; and
- one compile command still completes and publishes a coherent review map.

## Decision

Do not spend the next implementation cycle on a standalone NPW import, a new composite
network score, or more review-map colour logic. The first delivery must repair the
production planning chain in this order:

1. production candidate facts and configurable candidate discovery;
2. selection of all strategic unit types;
3. immutable application into the authoritative review network;
4. graph/matching/demand improvements; then
5. bounded external or AI assistance.

That order turns the already-developed governance and display features into actual
network decisions and directly addresses the observed preference for A/main roads over
cheaper existing active-travel assets.
