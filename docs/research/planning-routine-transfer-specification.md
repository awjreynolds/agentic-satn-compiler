# Planning routine transfer specification

- Date: 4 August 2026
- Resolution of: GitHub issue 317
- Parent Wayfinder map: GitHub issue 300
- Status: build-ready specification; no implementation or ADR acceptance

This specification turns the source-level research into implementation packages.
It is grounded in:

- [the current SATN routine/gap trace](satn-planning-routine-gap-map.md);
- [the NPW executable-routine analysis](npw-planning-routines-deep-dive.md);
- [the NPT/corenet/osmactive/rnetmatch analysis](npt-planning-routines-deep-dive.md); and
- [the maintained graph/accessibility/matching analysis](external-planning-routines-deep-dive.md).

## Outcome

The next compiler should expose one deep planning module:

```python
def compile_strategic_network(
    request: StrategicNetworkPlanningRequest,
) -> StrategicNetworkPlanningResult:
    """Always return a complete, reviewable planning result."""
```

Callers provide governed evidence, policy profiles, finite obligations, and initial
Officer Decisions. They do not choose graph algorithms, call matching engines,
construct candidate facts, mutate route frames, or stitch publication layers. The
module returns the effective strategic network, all alternatives, decisions, gaps,
diagnostics, evidence requests, and immutable lineage.

Internally the module has four core stages and optional enrichment stages:

```text
Source Exports + profiles
          |
          v
 Planning Graph Snapshot -----> Graph diagnostics
          |
          v
 Line-evidence matches -------> Demand/corridor obligations (optional)
          |                                  |
          +------------------+---------------+
                             v
              Candidate discovery + assessment
                             |
                             v
          Preferred/Officer selection for every role
                             |
                             v
              Immutable strategic application
                             |
                             v
   Strategic map + alternatives + gaps + divergence + evidence
```

This is a deep module because the external interface is one request/result pair while
graph preservation, matching, finite path enumeration, evidence classification,
sectioning, selection, authority, fallback, and publication consistency stay local.

## Transfer decision matrix

| Source routine or pattern | Decision | Exact SATN use |
|---|---|---|
| NetworkX/OSMnx components, degree, circuity, reachability | **Adopt compiler-native** | Use the installed NetworkX/OSMnx implementation against a governed snapshot; emit observations, never delete geometry. |
| OSMnx `k_shortest_paths` / Yen method | **Adapt compiler-native** | Implement deterministic bounded materially-distinct path enumeration over SATN's edge-identity-preserving graph. Do not expose OSMnx route geometry as authority. |
| NPT/corenet segmentation, high-demand seeds and constrained paths | **Adapt compiler-native** | Produce optional finite `CorridorObligation` evidence; remove hidden thresholds, largest-component retention, repeated dangle deletion and row-order IDs. |
| `rnetmatch` shared-length matcher and extensive/intensive laws | **Adopt the method, initially reimplement in Python** | One provider-neutral matching module with projected-CRS validation, explicit candidates, shared lengths and aggregation laws. Benchmark the Rust implementation later. |
| `osmactive` infrastructure categories | **Adapt as a jurisdiction profile** | Keep raw claims and map them through a versioned England/Scotland policy adapter. Never import Scottish thresholds or missing-value assumptions as facts. |
| NPW high-LoS reachability and severance flood | **Adapt as diagnostics** | Emit reachable edges, barriers, crossing assumptions and witness paths. Do not author fixes or route geometry. |
| NPW route autosplit | **Adapt internally** | Split candidates at governed evidence transitions to populate intervention state, alignment basis, effort and fragmentation; expose sections in the result. |
| NPW route editing and state model | **Adapt the interaction pattern** | Officer choices select compiler-authored candidate/section IDs. Preserve atomic ledgers, prior result, stale-target diagnostics and divergence. |
| NPW `fix_unreachable_poi`, freehand fallback and browser save format | **Reject** | A disconnect remains a gap or finite compiler candidate; UI/local state never becomes evidence or canonical geometry. |
| corenet largest-component and repeated dangle pruning | **Reject** | Retain every component and exclusion record. Small or disconnected assets may be strategically valuable. |
| NPT/NPW hidden percentiles, ckmeans tiers, road multipliers and LoS scores | **Reject as defaults** | They may appear only in named, fingerprinted sensitivity profiles with raw dimensions still visible. |
| R5/r5r accessibility | **Governed external prototype after the core path** | Pinned offline scenario comparison; immutable observations only. No live service, demand claim or route winner. |
| Valhalla/FMM/OSRM matching | **Governed external prototype** | Offline comparator for difficult trace-to-edge cases; disagreement creates review evidence. |
| OTP wheelchair/transit routing | **Later governed external prototype** | Use only where scheduled transit or accessibility comparison is an explicit officer question. |
| `parenx` skeleton/Voronoi | **Later display-only prototype** | A non-authoritative corridor simplification; never replace source or selected geometry. |
| AI route authoring, scoring or fact generation | **Reject** | AI may explain/cite/triage only after deterministic compilation. |

## Shared invariants

Every package below must preserve these invariants.

1. **Compilation completes.** Every declared domain/evidence/search/runtime failure
   produces a typed result, explicit gap, evidence request, or deterministic fallback.
   Only process corruption, resource exhaustion outside the declared work budget, or a
   programmer invariant violation may escape as an exception; atomic publication then
   retains the prior release.
2. **Policy is data.** Thresholds, strategy orders, work budgets, matching tolerances,
   fallback orders, and sensitivity profiles are frozen configuration with content
   fingerprints. Engine code contains validation bounds, not transport policy values.
3. **Facts are claim-specific.** Existing provision, legal access, route opportunity,
   traffic, speed, gradient, width, land constraint, safety and deliverability are
   independent observations. Missing/conflicting never becomes zero or favourable.
4. **Discovery is not selection.** Search costs expose materially different options;
   they do not decide the Preferred Strategic Alignment.
5. **No silent deletion.** Every supplied obligation, generated candidate, unmatched
   observation, disconnected component, rejected option and fallback has a stable
   disposition.
6. **Geometry authority stays local.** Canonical geometry is materialised only from
   governed SATN graph edge chains or an explicit no-geometry gap.
7. **Officer decisions are initial inputs.** Exact valid decisions apply without
   expiry. A different compiler preference produces a visible divergence.
8. **Map semantics are projections.** Core/halo/pattern, legend, details, GeoJSON,
   GeoPackage and PDF derive from the same immutable result.

## Package 1 — edge-identity-preserving Planning Graph

### Problem and source transfer

`RoadGraph` currently uses a `DiGraph`, collapses parallel `(u,v)` edges to the
shortest, retains few edge facts, and removes smaller components from attachment search
when one component reaches 90%. Transfer the MultiDiGraph/component discipline from
OSMnx/NetworkX, but retain SATN's canonical IDs, route controls and deterministic
traversal.

### New module and interface

Own `src/satn/planning_graph.py`:

```python
@dataclass(frozen=True)
class PlanningGraphRequest:
    routable_edges: SourceExportFrame
    asset_observations: tuple[EvidenceObservation, ...]
    road_observations: tuple[EvidenceObservation, ...]
    route_controls: RouteControlSet | None
    profile: PlanningGraphProfile

@dataclass(frozen=True)
class PlanningGraphSnapshot:
    graph_fingerprint: str
    edge_records: tuple[PlanningEdgeRecord, ...]
    node_records: tuple[PlanningNodeRecord, ...]
    component_records: tuple[GraphComponentRecord, ...]
    observation_matches: tuple[EdgeObservationBinding, ...]
    diagnostics: tuple[GraphDiagnostic, ...]

def build_planning_graph(request: PlanningGraphRequest) -> PlanningGraphSnapshot: ...
```

The NetworkX `MultiDiGraph`, spatial indexes and path caches are private
implementation. Callers see canonical records and invoke path discovery only through
Package 3. `RoadGraph` remains a compatibility adapter for legacy Backbone assembly
during cutover and is deleted or reduced once Package 4 owns the effective network.

### Required edge facts

Each directed edge record carries:

- stable source and directed-edge IDs;
- canonical geometry and length;
- `highway`, `ref`, `oneway`, bicycle/foot/access claims and their source IDs;
- alignment-basis observations (`cycleway`, `greenway`, `current-ncn`,
  `reclassified-ncn`, `prow-footpath`, `prow-bridleway`, `quiet-road`, A/B/other road);
- intervention observations (`existing-provision`, `upgrade-required`,
  `proposed-new-link`, unknown/conflicting);
- traffic, speed, width/protected-space, gradient and constraint observation IDs;
- reciprocal-edge state; and
- weak/strong component identity.

No single flattened “quality” or “LoS” value belongs in this record.

### Configuration

`PlanningGraphProfile` contains the source network filter/profile fingerprint,
canonical CRS, attachment distances, legal-access interpretation profile, and route
control policy. Preserving parallel edges and reporting every component are correctness
invariants and are not configurable off switches.

Remove the embedded dominant-component attachment exclusion. If a deployment wants to
limit a particular search, it supplies explicit eligible component IDs or a component
policy; the result still includes excluded component/affected-place diagnostics.

### Failure and completion

- Invalid/missing optional tag observations become unknown edge claims.
- Geometry that cannot form a canonical edge is retained in a rejected-edge record.
- A duplicate stable edge ID with conflicting geometry becomes a blocking evidence
  diagnostic for that edge, not a whole-run crash.
- Invalid required Source Export identity yields a terminal planning result with the
  prior ordinary/reference network and no newly authored geometry.

### Fixture and exact assertions

Use a projected graph with two parallel A–B edges (A-road carriageway and cycleway),
B–C reciprocal edges, C–D one-way edge, and isolated E–F cycleway.

- Both A–B edges survive with distinct stable IDs under input permutation.
- Component sizes and reciprocal states are exact and sorted.
- E–F is attachable/diagnosable and never silently removed by component share.
- Raw/conflicting access and asset observations survive independently.
- Reversing source rows produces the same graph fingerprint.
- Legacy direct path parity is proven for fixtures without parallel edges.

## Package 2 — governed line-evidence matching and aggregation

### Problem and source transfer

Current asset/NCN binding relies on buffered overlap and, at one seam, on the route
role that happened to discover a path. Adapt `rnetmatch`'s distance/angle/shared-length
method and its separate extensive/intensive aggregation laws. Do not add an R runtime
or Rust FFI to the first implementation.

### New module and interface

Own `src/satn/line_evidence_matching.py`:

```python
def match_line_evidence(
    sources: tuple[LineEvidenceRecord, ...],
    targets: tuple[TargetLineRecord, ...],
    profile: LineMatchProfile,
) -> LineMatchResult: ...

def aggregate_line_evidence(
    matches: LineMatchResult,
    observations: tuple[NumericObservation, ...],
    profile: LineAggregationProfile,
) -> AggregatedLineEvidence: ...
```

`LineMatchResult` includes accepted, ambiguous, conflicting and unmatched rows with
source/target IDs, distance, angle, source/target/shared length, coverage fractions,
orientation state, reason, CRS/profile/evidence fingerprints, and a stable result hash.

The aggregation interface supports only declared laws:

- `extensive`: `sum(value * shared_length / source_length)`;
- `intensive`: shared-length-weighted mean;
- `maximum`/`minimum`: only for a named observation whose schema permits it; and
- `categorical-proportion`: shared length by category / matched target length.

There is no arbitrary callback aggregation interface.

### Initial trial profile

The checked-in trial profile uses EPSG:27700, 15 m candidate distance, 35° bearing
tolerance, 10 m minimum shared length, explicit orientation-insensitive matching, and
no automatic “best” resolution where two sources materially conflict. These are
profile data derived from NPT's operational calls, not national standards; BaNES must
be able to run 5/15/25 m sensitivity profiles before adoption.

### Fixture and exact assertions

Target: 100 m east–west line. Sources: an exact 30 m section, a parallel 70 m section
5 m away, a 90° crossing, a reversed duplicate and an unmatched line.

- Exact/parallel matches report shared lengths 30/70; the crossing and distant line do
  not contribute.
- Reversal follows the explicit orientation policy and never changes IDs/totals.
- Extensive values 10/20 aggregate to 17 over the 100 m target.
- Conflicting duplicates remain conflict records.
- x-only versus xy spatial-index implementations, if later added, must produce the
  same semantic result.
- Input permutation leaves the canonical result unchanged.

### Later benchmark gate

Only if the Python implementation breaches the declared BaNES work budget should a
Rust `rnetmatch` adapter be prototyped. Two adapters (Python and Rust) would then make
the seam real; both must pass the same interface fixture before Rust can be selected.

## Package 3 — finite candidate discovery and complete candidate facts

### Problem and source transfer

Replace the four hard-coded weighted shortest paths and the route-role-dependent asset
classification. Adapt OSMnx/Yen materially different paths, corenet constrained path
seeding, and NPW route autosplit into one deep candidate module.

### New module and interface

Own `src/satn/candidate_discovery.py`:

```python
@dataclass(frozen=True)
class CandidateDiscoveryRequest:
    graph: PlanningGraphSnapshot
    obligations: tuple[CorridorObligation, ...]
    evidence_snapshot: GovernedEvidenceSnapshot
    profile: CandidateDiscoveryProfile

@dataclass(frozen=True)
class CandidateDiscoveryResult:
    candidate_sets: tuple[AlignmentCandidateSet, ...]
    candidate_records: tuple[AssessedCandidateRecord, ...]
    obligation_dispositions: tuple[CorridorObligationDisposition, ...]
    search_diagnostics: tuple[CandidateSearchDiagnostic, ...]
    evidence_requests: tuple[EvidenceRequest, ...]
    fingerprint: str

def discover_candidate_sets(
    request: CandidateDiscoveryRequest,
) -> CandidateDiscoveryResult: ...
```

The interface replaces production calls to `choose_alignment` for strategic planning.
Legacy Backbone may continue using `RoadGraph` during migration, but its paths are only
reference candidates, never the sole candidate universe.

### Search strategies

Each configured strategy declares an ordered tuple of non-negative additive edge-cost
dimensions. The implementation uses tuple-cost deterministic label setting and bounded
Yen-style deviations; it does not collapse dimensions into an undocumented composite
score.

The initial trial profile declares:

1. `minimum-distance`: `(length_m)`;
2. `reuse-first`: `(proposed_new_link_m, major_road_new_provision_m,
   upgrade_required_m, mixed_traffic_m, length_m)`;
3. `off-carriageway-opportunity`: `(off_carriageway_deficit_m,
   proposed_new_link_m, length_m)`;
4. `low-traffic-non-a-road`: `(a_road_m, high_traffic_unprotected_m,
   mixed_traffic_m, length_m)`; and
5. `major-road-reference`: `(non_major_road_m, length_m)` so the expensive engineering
   alternative remains visible rather than being the implicit baseline.

The strategy order exposes alternatives only. Preferred selection still uses the
Network Selection Profile and may choose any admitted candidate.

Initial trial work limits are profile data:

- maximum three paths per strategy;
- maximum twelve generated material candidates per obligation;
- maximum five admitted options (existing `AlignmentCandidateSet` contract);
- discovery detour ceiling 2.0 times the direct path, separate from the lower selection
  displacement threshold;
- deterministic node-settlement/deviation budgets rather than a hardware-dependent
  wall-clock cutoff; and
- exact candidate suppression records when a budget, detour, duplicate or admission
  limit applies.

### Candidate assessment and NPW-style sectioning

For each edge chain, the module internally calls line matching and splits at:

- graph junction/declared choice point;
- primary alignment basis change;
- intervention-state change;
- governed access/constraint change;
- traffic/protected-space evidence change;
- network scope change;
- gradient band change; and
- officer-authored section boundary already present in the ledger.

It emits `CandidateReviewSection` records and populates every production vNext fact:

- `reuse_class` and the exact evidence used to derive it;
- `intervention_state` as the highest delivery burden present on a material section;
- `alignment_bases` and primary basis;
- metres/share by existing, upgrade, proposed, low-traffic and major-road state;
- total absolute elevation change;
- transition and fragmentation counts;
- traffic/constraint observations; and
- governed evidence/provenance IDs.

Extend `ReuseFirstCandidateClass` with `UNKNOWN_OR_CONFLICTING` and
`InterventionState` with `UNDETERMINED`. Unknown candidates remain inspectable and
cannot outrank evidenced candidates unless an explicit officer decision applies.

Classification depends only on evidence and route sections, never the search strategy
that found the route. Retain the generating strategy IDs solely as provenance.

### Selection profile cutover

BaNES moves to one vNext trial profile only after this producer passes the corpus. The
profile keeps the agreed reuse-first class order, independently orders intervention
states, uses total absolute elevation effort, retains unknown optional evidence, and
applies configured material displacement rules for detour, effort, fragmentation,
known prohibition and known constraints. DfT traffic is a challenge dimension, not a
veto.

Remove `_weight_for` and the 80%/150%/135% policy thresholds from the strategic
planning path. They may remain temporarily under an explicitly named legacy
compatibility profile and must disappear after the BaNES cutover.

### Failure and completion

- No attachment/path becomes a `CandidateSetGapEvidence` with endpoints and search
  diagnostics.
- Work-budget exhaustion retains generated candidates and records `search-truncated`;
  the configured deterministic fallback selects from the finite retained set.
- Missing asset/traffic/elevation/demand evidence produces unknown sections and
  evidence requests, never a zero-cost edge.
- A candidate with a known access prohibition remains in provenance but fails the hard
  gate.

### Acceptance fixture

The shared synthetic graph contains direct A-road, existing non-NCN cycleway,
declassified-NCN/PROW upgrade, quiet-road and disconnected-asset alternatives.

- All four connected alternatives are generated with stable IDs and correct section
  facts independent of strategy or input order.
- Existing cycle provision is preferred to the shorter A-road under the trial profile.
- A configured detour rule can displace it and emits an exact Material Displacement
  Record.
- PROW/declassified evidence is upgrade-required and never called existing provision
  without the required claim.
- Missing traffic/elevation stays unknown.
- The disconnected asset appears in graph/candidate diagnostics.

## Package 4 — selection of every strategic role and immutable network application

### Problem and source transfer

The public compiler currently selects only chained Spine Access candidates. Interurban
and Strategic Destination candidate sets are prepared but not consumed. Reviewable
selections are overlays over an already-complete Backbone. The existing Strategic
Reference replay proves that exact candidate edge chains can replace baseline spine
geometry, but its adoption authority must remain separate.

### New module and interface

Own `src/satn/strategic_network_planning.py` and deepen the reusable geometry work in
`strategic_reference_replay.py` behind an authority-neutral internal seam:

```python
@dataclass(frozen=True)
class StrategicNetworkPlanningRequest:
    area_fingerprint: str
    reference_network: CompiledNetworkReference
    discovery: CandidateDiscoveryResult
    criteria: tuple[PreparedCandidateCriteria, ...]
    selection_profile: NetworkSelectionProfile
    officer_decisions: OfficerDecisionLedger
    fallback_profile: StrategicPlanningFallbackProfile

@dataclass(frozen=True)
class StrategicNetworkPlanningResult:
    status: Literal["complete", "complete-with-gaps", "reference-fallback"]
    effective_network: EffectiveStrategicNetwork
    selections: tuple[EffectiveReviewableSelection, ...]
    candidate_sets: tuple[AlignmentCandidateSet, ...]
    reference_routes: tuple[ReferenceRoute, ...]
    unselected_candidates: tuple[CandidateDisposition, ...]
    gaps: tuple[ReviewableNetworkGap, ...]
    divergences: tuple[OfficerCompilerDivergence, ...]
    evidence_requests: tuple[EvidenceRequest, ...]
    diagnostics: tuple[PlanningDiagnostic, ...]
    lineage: StrategicPlanningLineage
    fingerprint: str
```

`EffectiveStrategicNetwork` contains exact selected `CandidateReviewSection` geometry
for Interurban Spine, Community/Spine Access and Strategic Destination Access, plus
required connectors and obligations. It is the authority for the strategic review-map
projection. The ordinary Backbone is retained as `reference_routes`, not mixed into
the effective selection.

### Application algorithm

1. Validate exhaustive obligation/candidate/criteria rosters by fingerprint.
2. Run `select_preferred_alignment` for every Candidate Set role.
3. Apply an exact Officer Decision where its logical target and candidate ID exist.
4. Retain the compiler preference and emit divergence where the officer differs.
5. Convert effective candidate edge chains to section geometry through one generic
   materialisation implementation shared with Strategic Reference replay.
6. Validate mandatory Network Place/destination obligations and section continuity.
7. Assemble the immutable effective network and retain every reference/unselected/
   rejected route separately.
8. Project map/package artefacts atomically from this one result.

The generic materialiser receives validated selection bindings and returns geometry;
it does not decide whether the authority is compiler-generated, officer-informed or an
adopted Strategic Reference. Authority records stay in their respective adapters.

### Fallback hierarchy

The profile declares, in order:

1. exact applicable Officer Decision;
2. compiler Preferred Strategic Alignment;
3. original governed reference route for that obligation, marked provisional;
4. explicit endpoint Network Gap.

No live/AI route, foreign geometry, or unrecorded shortest path appears in fallback.
A failure in one obligation cannot suppress other valid selections.

### Officer and map result

The default map opens with only:

- effective Strategic Active Travel Network, including community/destination
  connectors; and
- Places.

Every effective section uses one core/halo/pattern legend to expose both alignment
basis and intervention state: existing provision, upgrade required, proposed new link,
or undetermined. Unselected Candidates, Existing Assets, Upgradeable Assets, DfT
Traffic, Graph Diagnostics and Officer Divergence remain explicit optional layers.
The compiler-preferred route in a divergence uses a distinct non-grey state.

### Acceptance assertions

- Interurban and destination Candidate Sets appear in scenario selections, not merely
  preparation metadata.
- The existing non-NCN cycleway replaces the original A-road line in the effective
  network while the A-road remains an unselected proposed-major-infrastructure option.
- Selected section geometry and forward/reverse edge chains are exact.
- Officer choices never expire; divergence is visible and stable.
- One bad/missing obligation yields `complete-with-gaps`; all valid selections publish.
- GeoJSON, GeoPackage, PDF, map legend and details use the same result fingerprint.
- A replayed adopted Reference still carries its distinct adoption authority.

## Package 5 — demand-led Corridor Obligations

### Purpose and source transfer

This optional P1 package improves *which connections are investigated*. Adapt
NPT/corenet flow aggregation, segmentation, DBSCAN seed reduction and constrained
path seeding, while preserving SATN Network Places and explicit strategic destination
obligations. It is one input to Package 3, not a network generator.

### Interface

Own `src/satn/corridor_obligations.py`:

```python
def derive_corridor_obligations(
    places: tuple[NetworkPlace, ...],
    destinations: tuple[StrategicDestination, ...],
    gateways: tuple[CrossBoundaryGateway, ...],
    demand: tuple[MatchedDemandObservation, ...],
    graph: PlanningGraphSnapshot,
    profile: CorridorObligationProfile,
) -> CorridorObligationResult: ...
```

The result contains obligations, discarded seed/pair records, unmatched demand,
sensitivity profile IDs, and explicit fallback origin (`demand-led`,
`place-hierarchy`, or `coverage-only`).

### Algorithm changes from corenet

- Aggregate flows through Package 2 with declared extensive/intensive laws.
- Segment at configured metric length; no value suppression inside the algorithm.
- Use an explicit demand threshold or percentile profile and retain values below it.
- Cluster segment seeds with configured DBSCAN parameters; use a canonical medoid
  (minimum total distance then stable ID), never the first row.
- Pair seeds according to declared OD evidence, hierarchy/gateway rules and network
  distance. Do not make all-pairs the default.
- Retain disconnected islands, failed paths, over-distance pairs and low-demand seeds
  with dispositions.
- Generate stable obligation IDs from endpoints, evidence and profile fingerprints.

### Completion

With no usable demand evidence, return place-hierarchy/coverage obligations and an
evidence request. Compilation does not stop and demand is never inferred from nearby
population.

### Fixture

Three flow-bearing chains (two clustered, one independent), a below-threshold bridge,
one unmatched flow, one gateway and one disconnected high-demand island. Assert stable
medoids/IDs, explicit sensitivity changes, retained island/failures, and a finite
obligation roster smaller than the complete Cartesian pair set.

## Package 6 — graph quality, reachability and severance evidence

### Purpose and source transfer

Adapt OSMnx/NetworkX metrics and NPW's reachable/severance flood into a diagnostic-only
module. It helps officers understand why a proposed network is coherent or blocked; it
does not repair, score, or select the network.

### Interface

Own `src/satn/network_diagnostics.py`:

```python
def analyse_network(
    graph: PlanningGraphSnapshot,
    network: EffectiveStrategicNetwork,
    obligations: tuple[CorridorObligation, ...],
    profile: NetworkDiagnosticProfile,
) -> NetworkDiagnosticResult: ...
```

Output families remain separate:

- weak/strong components, degree/dangles and bridge/cut edges;
- reciprocal-access and component-exclusion findings;
- directness/circuity with explicit denominator and failed OD pairs;
- reachable edges/places under a named provision/LoS profile;
- severance edges, crossing assumptions and affected obligations;
- shortest canonical witness paths and exact no-path findings; and
- before/after release or scenario deltas.

The NPW crossing rule is not copied as truth. `NetworkDiagnosticProfile` must declare
which claim states permit traversal/crossing and how unknown is handled. Default
unknown behaviour is `retain-as-unknown-and-report`, never “High LoS” or traversable.

### Fixture

Four-node main component, two-node island, one dangle, one cut edge, one low-provision
barrier, an explicitly evidenced crossing and two equal witness paths. Assert exact
component/cut/dangle IDs, stable tie handling, reachable set, severance witness,
affected obligation, and no geometry mutation.

## Package 7 — Governed External Analysis Runs

### Scope

Only after Packages 1–6 should external engines be prototyped. Use a true external
port with at least a production adapter and deterministic fixture adapter:

```python
class ExternalAnalysisAdapter(Protocol):
    def run(self, request: ExternalAnalysisRequest) -> ExternalAnalysisResponse: ...

def run_governed_external_analysis(
    request: ExternalAnalysisRequest,
    adapter: ExternalAnalysisAdapter,
) -> GovernedExternalAnalysisRun: ...
```

Every run records source/export hashes, engine version/commit/licence, environment,
full parameters/defaults, CRS/timezone/date, seed/thread policy, warnings, null/
unreachable/unmatched rows, raw output hash and normalized observation hash.

Prototype order:

1. R5/r5r opportunity-accessibility comparison;
2. Valhalla or FMM observation matching comparator;
3. OTP only for a declared transit/wheelchair question; and
4. OSRM as a second matcher comparator where disagreement evidence adds value.

An invalid/timeout/unavailable external run produces `unavailable` observations and
the core result continues unchanged. External geometry, cost, LoS or accessibility is
never canonical and never a route winner.

## Package 8 — bounded AI review assistance

### Scope

Use the existing Agent Runtime/fallback discipline only when deterministic evidence
shows a material conflict or missing investigation. The interface is:

```python
def assist_review(
    packet: ReviewDecisionPacket,
    runtime: ReviewAssistantRuntime,
) -> ReviewAssistanceRecord: ...
```

The packet contains finite candidates, separate evidence dimensions, exact citations,
configured allowed actions and deterministic fallback. Allowed responses are:

- explain a material comparison using cited packet evidence;
- request one of a closed set of evidence investigations;
- identify a cited inconsistency; or
- choose one of the offered actions only where the deterministic workflow explicitly
  delegates that ambiguity.

Validation requires every citation to be a packet evidence ID and every action to be
offered. The runtime cannot add geometry, facts, values, thresholds, scores or options.
Invalid, uncited, timed-out or unavailable responses use the same configured fallback
and compilation completes.

## Initial trial configuration

The implementation tickets should land a checked-in trial profile rather than engine
constants. Illustrative shape:

```yaml
planning:
  graph_profile: banes-planning-graph-trial-v1
  line_matching:
    profile_id: banes-line-match-trial-v1
    distance_tolerance_m: 15
    angle_tolerance_degrees: 35
    minimum_shared_length_m: 10
    ambiguity_policy: retain-conflict
  candidate_discovery:
    profile_id: banes-candidate-discovery-trial-v1
    strategies:
      - minimum-distance
      - reuse-first
      - off-carriageway-opportunity
      - low-traffic-non-a-road
      - major-road-reference
    maximum_paths_per_strategy: 3
    maximum_generated_candidates_per_obligation: 12
    maximum_admitted_candidates_per_set: 5
    discovery_detour_ratio: 2.0
    maximum_node_settlements: 250000
  network_selection:
    contract: satn-network-selection-profile/vNext
    profile_id: banes-reuse-first-trial-v1
    version: 1
    unknown_value_policy: retain-and-request-evidence
    deterministic_tie_break: stable-candidate-id
  fallback:
    hierarchy:
      - officer-decision
      - compiler-preferred
      - governed-reference-route
      - network-gap
```

These are recommended trial values and must be sensitivity-tested on the synthetic
corpus and BaNES visual review before adoption. They are not LTN 1/20, Rural Design
Guide, or national minimum values.

## Implementation order and native dependencies

1. **Planning Graph** — no package dependency; first because every later routine needs
   preserved edge identity and components.
2. **Line Evidence Matching** — depends on Planning Graph records.
3. **Candidate Discovery and Facts** — depends on Planning Graph and Line Matching.
4. **Strategic Selection/Application** — depends on Candidate Discovery; this is the
   first end-to-end feature release and must publish the BaNES proving map.
5. **Demand-led Obligations** — depends on Line Matching and the Package 3 obligation
   interface; it extends candidate scope without blocking the P0 cutover.
6. **Network Diagnostics** — depends on Planning Graph and Effective Strategic Network.
7. **Governed External Runs** — depends on stable evidence/diagnostic/result contracts.
8. **AI Assistance** — depends on deterministic selection, diagnostics and fallback.

Packages 1–4 are one P0 feature programme. A partial production cutover before Package
4 is prohibited: planning artefacts may be published for developers, but the user-facing
claim that BaNES uses reuse-first planning is false until selected Interurban, Access
and Destination routes form the effective strategic network.

### Implementation tickets

| Package | Ticket | Priority and dependency |
|---|---|---|
| Planning Graph | [#319](https://github.com/awjreynolds/agentic-satn-compiler/issues/319) | P0 root |
| Line Evidence Matching | [#320](https://github.com/awjreynolds/agentic-satn-compiler/issues/320) | P0; blocked by #319 |
| Candidate Discovery and Facts | [#321](https://github.com/awjreynolds/agentic-satn-compiler/issues/321) | P0; blocked by #319 and #320 |
| Strategic Selection/Application | [#322](https://github.com/awjreynolds/agentic-satn-compiler/issues/322) | P0 release boundary; blocked by #321 |
| Demand-led Corridor Obligations | [#323](https://github.com/awjreynolds/agentic-satn-compiler/issues/323) | P1; blocked by #320 and the #321 obligation interface |
| Network Diagnostics | [#324](https://github.com/awjreynolds/agentic-satn-compiler/issues/324) | P1; blocked by #319 and #322 |
| Governed External Analysis Runs | [#325](https://github.com/awjreynolds/agentic-satn-compiler/issues/325) | P2; blocked by #322 and #324 |
| Bounded AI Review Assistance | [#326](https://github.com/awjreynolds/agentic-satn-compiler/issues/326) | P2; blocked by #322 and #324 |

All eight are native subissues of the Wayfinder map and use native blocking
relationships. Each ticket owns a bounded module surface, fixture and focused
acceptance assertions; none requires importing an external application's state model
or regional dataset.

## Release gate

The P0 programme is done only when one checked-in synthetic scenario and one BaNES
compile prove:

- a non-NCN existing cycleway can enter discovery and defeat a shorter A-road;
- PROW/declassified routes are visible as upgrade opportunities;
- every hard-coded strategic routing weight/threshold is absent or confined to an
  explicitly named legacy profile;
- all strategic role Candidate Sets are selected and applied;
- the effective map opens with Strategic Network plus Places only;
- discarded candidates, asset basis, intervention state, traffic, gaps and divergence
  are optional but inspectable layers;
- Officer Decisions apply without expiry and divergence remains highlighted;
- missing optional evidence/runtime/search capacity produces typed output; and
- generation completes, artefacts validate, and publication is atomic.

## Explicit non-goals

- importing Scottish datasets into an English deployment;
- reproducing the NPW application, Svelte state, PMTiles pipeline or save schema;
- creating one coherent-network/LoS/LTN score;
- assuming a PROW is legally cycleable, wide, safe, feasible or deliverable;
- treating population near a line as demand;
- silently repairing OSM connectivity;
- deleting disconnected components or dangles;
- direct live calls to hosted routing/accessibility services;
- accepting external or AI-authored canonical geometry; or
- publishing “national compliance” where guidance is an evidence consideration, not a
  veto.
