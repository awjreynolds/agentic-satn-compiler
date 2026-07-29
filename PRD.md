# Product Requirements: Spine-led Strategic Active Travel Network

**Status:** Draft for implementation

**Date:** 22 July 2026

**Product:** `banes-satn`

**Reference domain language:** [CONTEXT.md](CONTEXT.md)

## 1. Summary

Replace the current community-to-community network generation with a spine-led model.
The compiler must first establish a small number of continuous, strategically useful
backbones, then grow access outward to rural communities and schools. In urban areas,
it must describe circulation using classified roads and candidate low-traffic areas
rather than inventing cycle-route centrelines through residential streets.

This PRD defines the deterministic network core of an agentic (AI) compiler. A
configured AI runtime may assist with bounded, evidence-citing comparison of
compiler-authored choices. It does not invent evidence or executable geometry,
adopt policy, approve designs or replace accountable officer judgement. The
published POC uses the deterministic `fake` agent provider rather than a live
production AI service.

The resulting review map is an informative strategic visualization. It must show:

- rural A-road and established National Cycle Network (NCN) spines;
- iterative community and school access to those spines;
- urban classified-road circulation boundaries and Candidate Low-Traffic Areas; and
- local gradient challenges on every generated network edge.

The product is not a final scheme design, a statement that an A road is cycle-ready,
or a promise that every intervention is feasible or funded.

## 2. Problem

The existing compiler begins with pairwise Community Connections and later repairs
components and unexplained termini. This can produce a visually noisy spider's web
of locally plausible links without expressing a realistic delivery strategy.

Local authorities are more likely to deliver a high-quality shared backbone and then
connect nearby places to it over successive funding cycles. The generated network
therefore needs to communicate an order of growth:

1. establish strategic spines;
2. connect the nearest reachable obligations;
3. extend through already served communities into the rural hinterland; and
4. allow branches rooted at different spines to meet where this produces a useful
   transverse route, without creating a general mesh.

Urban areas need a different abstraction. Through motor traffic should use classified
roads. The unclassified street fabric between those roads should be represented as
Candidate Low-Traffic Areas, not as arbitrary preferred cycling lines.

## 3. Product outcome

Generate a council-portable Backbone-and-Access Network that is quieter, more
legible, more consistent with incremental delivery and still complete enough to
expose who or what is not served.

A successful compilation lets a reviewer answer:

- Where are the strategic rural and urban spines?
- How does each rural community and school reach the growing backbone?
- Where have branches created useful cross-spine traversal?
- Which urban areas should exclude through motor traffic?
- How can an urban school reach the boundary network through low-traffic fabric?
- Where will gradient make an edge harder in either direction?
- Which obligations or alignments remain unresolved?

## 4. Users and jobs

### Primary users

- Local-authority transport planners exploring a strategic network.
- Community and political stakeholders reviewing priorities and gaps.
- Technical reviewers inspecting the evidence and generated topology.

### Core jobs

- Understand the proposed network structure without reading source data or code.
- Identify a plausible sequence for building access outward from shared corridors.
- Inspect why a place was attached to a particular branch or spine.
- Compare a difficult access alignment with a longer, easier alternative.
- Find gaps and questions that require later investigation.

## 5. Product principles

1. **Backbone before branches.** Shared strategic corridors govern network growth.
2. **Reachability before proximity.** Attachments use the plausible cycling network,
   not straight-line distance.
3. **One useful edge before a mesh.** Add only edges that provide backbone access or
   a justified cross-spine meeting.
4. **Obligations may be leaves.** A served community or school may legitimately have
   degree one.
5. **Urban areas are circulation areas.** Do not invent preferred centrelines inside
   Candidate Low-Traffic Areas.
6. **Topography informs rather than prohibits.** Gradient can cause comparison but
   does not remove a spine.
7. **Evidence and proposals remain distinguishable.** An A-road spine is strategic
   linework requiring engineering, not evidence of present cycling quality.
8. **Show uncertainty.** Missing evidence or failed reachability becomes visible,
   never a fabricated straight line.
9. **Keep the map quiet.** Context belongs in independently selectable layers with
   accessible legends.
10. **Deterministic baseline first.** Compile governed evidence and declared general
    rules with an empty accepted-decision ledger and no feature-specific
    discretionary directives. Preserve this clean baseline for comparison.
11. **Human decisions are explicit scenarios.** Record the responsible officer or
    role, organisation, date, rationale and evidence for every accepted material
    decision in a new immutable Scenario Compilation; never silently overwrite the
    baseline or expose unnecessary personal data in public artifacts.
12. **Agents support but do not govern.** Bounded, evidence-citing agents may refine
    and compare finite compiler-authored choices. Agent output is advisory,
    schema-validated and retained with evidence references and explicit unknowns.
    Agents cannot invent evidence, create executable geometry or make policy
    decisions outside those contracts. Only deterministic compiler actions change a
    scenario, and material choices follow the configured human acceptance route;
    unresolved material ambiguity becomes a Human Intervention Request.

## 6. Scope

### 6.1 Strategic spines

#### Rural

- Admit every in-scope A-road corridor as a Strategic Spine.
- Admit every established NCN route as a Strategic Spine.
- Do not admit rural B roads merely because they are classified.
- Preserve continuous spine identity through the governed study area and relevant
  Cross-Boundary Gateways.
- Treat every A-road spine as requiring major engineering by default. The strategic
  line represents safe, generous provision alongside the road, not cycling in its
  carriageway and not a final facility design.
- Keep an NCN route in the backbone even where its present quality is deficient or
  uncertain. A quality gap must remain visible.

#### Urban

- Admit A roads, B roads and officially Classified Unnumbered Roads as Urban
  Main-Road Spines.
- Use official road classification where governed data is available. OSM functional
  highway tags may supplement but must not silently redefine official classification.
- Treat Urban Main-Road Spines as the corridors assigned to through motor traffic and
  future protected cycling provision.
- Keep urban NCN geometry visible as separate permeability and access evidence. It
  must not become a through-traffic boundary solely because it is NCN.

### 6.2 Rural backbone-outward assembly

- Seed network growth from all Strategic Spines concurrently.
- Use Community Reference Points for this product version; do not require settlement
  footprints or entry through the centre of every inhabited area.
- At each growth step, select the nearest reachable unserved Access Obligation from
  the existing spine or served branch frontier using plausible cycling-network cost.
- Allow a Community to attach through an already served adjacent Community when that
  is the most plausible route toward a spine.
- Give each accepted attachment a stable identifier, parent branch, root spine and
  evidence-backed rationale.
- Use deterministic tie-breaking so identical governed inputs produce identical
  topology.
- Continue until every Access Obligation is served or represented by a Network Gap.

The compiler must not:

- generate all community pairs;
- require every Community to connect directly to a spine;
- repair a legitimate degree-one Community merely because it is a terminus; or
- draw an unroutable straight line to create visual completeness.

### 6.3 Cross-spine traversal

- Detect adjacency between Communities on branches rooted at different Strategic
  Spines.
- Add the first justified Branch Meeting Connection between meeting growth fronts.
- Promote the resulting continuous transverse chain to a Cross-Spine Connector.
- Avoid parallel meeting edges between the same fronts unless the existing meeting
  becomes unusable or a separate connection is required for network completeness.
- Do not impose a connector quota or independently attach every middle Community to
  both parallel spines.

This must allow communities between two broadly parallel spines to gain meaningful
access to both corridors through the emergent community chain.

### 6.4 School access

- Treat primary, secondary, all-through and special schools as mandatory School
  Access Obligations.
- Keep colleges and universities as context unless separately configured as
  Strategic Destinations.
- Prefer a mapped usable entrance. Otherwise record an entrance as inferred or
  unresolved; do not silently snap the school centroid to the nearest road.

For rural schools:

- connect the usable School Access Point to the nearest reachable spine, connector or
  already established access branch;
- reuse a community access alignment where this is topologically and geographically
  sensible; and
- do not generate school-to-school or school-to-community journey objectives.

For urban schools:

- consider the obligation served when its usable entrance connects through
  continuous low-traffic street or path fabric to a portal on an Urban Main-Road
  Spine;
- represent that fabric as area permeability rather than a selected residential
  street centreline; and
- expose an unresolved entrance or discontinuous path as a visible finding.

### 6.5 School Street candidates

- Produce a preliminary Green/Promising, Amber/Needs Investigation, Red/Unlikely or
  Grey/Not Evaluated marker for each in-scope School.
- Consider usable entrance location, adjoining road classification, bus and essential
  access, alternative through-traffic routes and displacement evidence.
- Do not mark a school Green or Red using an inferred entrance alone.
- Present the result as qualitative evidence for investigation, not feasibility or a
  calibrated probability.

### 6.6 Urban circulation and Candidate Low-Traffic Areas

- Derive Candidate Low-Traffic Areas from connected unclassified-street fabric
  enclosed by Urban Main-Road Spines and, where required, stable non-road settlement
  edges.
- Allow a built-up edge adjoining open land, major river, canal or railway to close a
  circulation area.
- Do not use administrative wards, property lines or individual field parcels as
  boundaries by themselves.
- Treat observed internal through traffic as an intervention need, not a reason to
  promote an unclassified street to a spine.
- Preserve named portals between each Candidate Low-Traffic Area and its boundary
  network.
- Do not claim that a Candidate Low-Traffic Area is an existing LTN.

### 6.7 Topography

- Produce a Topography Profile for every generated edge, including Strategic Spine
  sections, Cross-Spine Connectors and access connections.
- Sample elevation throughout an alignment. Do not infer difficulty from endpoint
  elevation difference.
- Record route distance and, for each direction, cumulative ascent, cumulative descent
  and steepest sustained gradient.
- Segment each edge into adjustable gradient bands:

| Band | Gradient |
| --- | ---: |
| Gentle | up to 3% |
| Noticeable | above 3% to 5% |
| Steep | above 5% to 8% |
| Very Steep | above 8% to 12.5% |
| Severe | above 12.5% |

- Display Noticeable and short steeper sections without automatically changing the
  selected route.
- Initially trigger comparison with an easier Alignment Option when an edge contains:

| Condition | Sustained length |
| --- | ---: |
| Steep | at least 100 m |
| Very Steep | at least 50 m |
| Severe | at least 30 m |

- Also trigger comparison when repeated shorter climbs create materially greater
  cumulative ascent than a plausible alternative.
- Retain and visibly flag the original alignment when no materially easier plausible
  alternative exists.
- Never remove a Strategic Spine because it is steep.

The bands and trigger lengths are trial configuration, not permanent design standards.

### 6.8 Strategic network visualization

- Default to the smallest set of layers needed to understand the backbone and access
  structure.
- Provide independently selectable layers for at least:
  - rural Strategic Spines;
  - urban Main-Road Spines;
  - Spine Access Branches and Cross-Spine Connectors;
  - Communities and Schools;
  - Candidate Low-Traffic Areas;
  - urban NCN evidence;
  - Gradient Sections; and
  - Network Gaps and findings.
- Show an accessible, text-labelled Layer Legend whenever a layer is active.
- Use a sequential terrain palette for gradient. Do not reuse Criterion Status
  red/amber/green semantics for gradient severity.
- Make feature state and rationale available in accessible HTML so neither people nor
  browser agents must infer meaning from colour or canvas geometry.
- Preserve the existing static, backend-free Review Map Bundle and authoritative
  spatial outputs unless a later implementation decision requires a compatible
  schema version change.

## 7. Evidence

The initial governed evidence set is:

- OpenStreetMap for routable street/path geometry, communities, schools, entrances,
  gates and contextual access evidence;
- Walk Wheel Cycle Trust published data for established NCN geometry;
- OS Open Roads or equivalent governed local-authority data for official A, B and
  Classified Unnumbered road classification; and
- Environment Agency national terrain-model data for continuous elevation coverage,
  with sparse OSM `ele` and `incline` values used only as corroboration.

Each compilation must record source identity, retrieval or effective date, licence,
content fingerprint and the evidence version used by every derived layer.

## 8. Functional requirements

| ID | Requirement |
| --- | --- |
| FR-01 | The same governed inputs and configuration produce the same network topology and stable identifiers. |
| FR-02 | Every admitted Strategic Spine is continuous or has an explicit, localized Network Gap. |
| FR-03 | Every Access Obligation is served according to its rural or urban rule, or is exposed as a Network Gap. |
| FR-04 | A degree-one served Access Obligation does not trigger an automatic repair edge. |
| FR-05 | Every Community Connection extends backbone access or forms part of a Cross-Spine Connector. |
| FR-06 | The compiler does not enumerate or publish arbitrary all-pairs or nearest-neighbour Community Connections. |
| FR-07 | Differently rooted rural branches may create one deterministic first-meeting connection. |
| FR-08 | Rural B roads are not Strategic Spines unless another admitted spine, such as NCN, follows them. |
| FR-09 | Urban unclassified streets are not promoted to through-traffic spines by observed traffic or route convenience. |
| FR-10 | Urban school access is assessed through area permeability without publishing an invented residential centreline. |
| FR-11 | Every published edge has a Topography Profile or a visible Grey evidence-unavailable state. |
| FR-12 | Gradient alternative search follows the configured severity-duration triggers and records the comparison. |
| FR-13 | A steep spine remains in the network and displays its challenge. |
| FR-14 | Every map colour and symbol has a visible text label when its layer is active. |
| FR-15 | Generated GeoPackage, GeoJSON, map, run records and agent records agree on stable feature identifiers. |
| FR-16 | Routine attachment, refinement and topography comparisons complete without per-edge human approval; an unresolved material ambiguity emits a structured Human Intervention Request. |

## 9. Acceptance scenarios

The implementation must include deterministic fixtures covering these scenarios:

1. **Single rural spine:** several villages form bounded branches to one A-road spine
   without pairwise links between every village.
2. **Community chaining:** a hinterland village reaches the spine through a nearer,
   already served village.
3. **Parallel spines:** branches grown from two parallel spines meet once and create a
   traversable Cross-Spine Connector without a local mesh.
4. **Legitimate leaf:** the last rural community remains degree one and Complete.
5. **Unreachable obligation:** a community or school with no plausible connection is
   represented by a Network Gap, not a straight line.
6. **Rural school reuse:** a school joins an existing access branch rather than causing
   a duplicate route to the spine.
7. **Urban circulation cell:** classified roads and a stable settlement edge enclose a
   Candidate Low-Traffic Area while internal unclassified streets remain non-spines.
8. **Urban school:** a school inside a Candidate Low-Traffic Area is served through
   continuous low-traffic fabric without a published residential centreline.
9. **Net-zero hill:** an edge descending 100 m and climbing 100 m reports both changes
   despite zero endpoint elevation difference.
10. **Directional hill:** an edge that is predominantly downhill in one direction
    reports the corresponding climb in the reverse direction.
11. **Short pinch:** a short 3–5% or short steeper section is visible but does not
    automatically cause alternative selection.
12. **Sustained climb:** a configured gradient trigger produces an easier-route
    comparison; if no better route exists, the flagged original remains selected.
13. **Accessible legend:** activating Schools or Gradient Sections exposes every
    marker or band meaning in text and through keyboard-accessible controls.

## 10. Success criteria

- All admitted rural Communities and Schools are served or have explicit gaps.
- No unexplained community pair survives solely to repair a degree-one terminus.
- Every published connection has a traceable role in the backbone-and-access topology.
- The parallel-spine fixture creates cross-spine traversal with one branch meeting,
  not a spider's web.
- Urban output contains no selected through-traffic spine on an unclassified street.
- Every edge exposes topography evidence or an explicit unavailable state.
- Reviewers can understand the network, school access, urban circulation and gradient
  layers independently through their legends and details.
- Existing artifact-integrity, reproducibility, accessibility and publication tests
  remain passing or are replaced by equivalent tests for the versioned schema.

No arbitrary target is set for the number of edges. A quieter network is achieved by
the topology rules, not by optimizing toward a predetermined line count.

## 11. Out of scope

- Google Street View or other street-level imagery sampling.
- API-key management for imagery providers.
- Automated classification of roadside conditions at 100 m intervals.
- Detailed intervention derivation or scheme design.
- Cost estimates, funding bids or construction phasing.
- Traffic modelling, consultation and displacement assessment for LTNs or School
  Streets.
- A guarantee that published NCN geometry is high quality, Trust-owned or funded.
- Rural B-road spines independent of NCN.
- Mandatory access for colleges and universities unless separately configured.
- Exact preferred cycling centrelines through urban residential streets.
- A composite cycling-effort, demand or feasibility score.

Street-level inspection remains a possible later refinement for deriving intervention
evidence after the network-generation model is stable.

## 12. Delivery sequence

1. **Spine and obligation model:** introduce versioned feature roles and governed
   evidence for rural/urban spines, Community Reference Points and Schools.
2. **Backbone-outward compiler:** replace pairwise nomination and terminus repair with
   deterministic concurrent growth, branch roots and first-meeting connections.
3. **Urban circulation:** derive classified-road boundaries, Candidate Low-Traffic
   Areas, portals and area-based school access.
4. **Topography:** enrich every edge, generate Gradient Sections and compare triggered
   alternatives.
5. **Publication:** update spatial schemas, review-map layers, accessible legends,
   PDF and cross-artifact validation.
6. **B&NES evaluation:** compare the previous and spine-led outputs for topology,
   gaps, visual noise and explainability without treating the previous output as the
   correct answer.

Each stage must have fixture coverage before it is enabled for the full B&NES
compilation.

## 13. Deferred decisions

These may be tuned using fixture and B&NES results without changing the product model:

- the precise network-cost tie-breaker for equally plausible attachments;
- the test for repeated shorter climbs with materially greater cumulative ascent;
- cartographic widths, opacity and default layer visibility;
- the governed method for inferring a School Access Point when no entrance is mapped;
  and
- when a separate cross-spine meeting is genuinely required after the first meeting.

Any change to spine eligibility, the meaning of an Access Obligation, urban through-
traffic classification or the prohibition on arbitrary pairwise links changes the
product model and requires an explicit decision rather than threshold tuning.
