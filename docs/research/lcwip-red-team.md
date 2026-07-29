# Red-team assessment: Oxfordshire SATN equivalence and an agentic LCWIP foundation

- Assessed: 2026-07-24
- Repository: [`awjreynolds/agentic-satn-compiler`](https://github.com/awjreynolds/agentic-satn-compiler)
- Scope: current repository, live publication, tests, existing GitHub issues, Oxfordshire's published SATN process, and current national LCWIP guidance
- Posture: independent challenge, not an implementation plan and not legal advice

## Executive verdict

Three different claims must not be collapsed into one.

| Claim | Verdict | Confidence | Reason |
| --- | --- | --- | --- |
| The B&NES SATN tool delivers functionality equivalent to Oxfordshire's SATN process | **No-go** | High | It implements a reproducible spine-led route topology and publication pipeline, but deliberately omits Oxfordshire's demand model, desire-line construction, SATN Index, segment scoring, consultation-led revision, officer/pipeline moderation, primary/complementary classification, and design toolkit. |
| The generated B&NES SATN can replace an LCWIP | **No-go** | High | The current product has neither a distinct walking-network method nor a prioritised programme of walking and cycling infrastructure improvements. It also lacks the governance, engagement, equality, policy-integration and adoption workflow expected of an LCWIP. Oxfordshire itself continues to use town LCWIPs alongside SATN. |
| The repository can be the foundation for building an LCWIP agentically | **Conditional go, as a subsystem** | Medium-high | Its evidence snapshots, deterministic compiler, explicit unknowns, bounded agent choices, stable identifiers, GIS artefacts and inspectable map are valuable foundations. They cover only part of an LCWIP workflow. The safe product shape is an LCWIP preparation system in which this compiler supplies governed network hypotheses and audit evidence, not an autonomous LCWIP author or adopter. |

The strongest safe statement today is:

> `banes-satn` is an experimental, auditable strategic-network hypothesis generator and review-map publisher. It is not an Oxfordshire-equivalent SATN process, an LCWIP, an adoption-ready plan, or evidence that a route is feasible.

That is consistent with the repository's own disclaimer and scope boundaries (`README.md:3-14`, `README.md:533-543`, `PRD.md:360-376`). Any stronger external claim would create false confidence.

## Evidence and method

This assessment used:

- the domain language in `CONTEXT.md`, including the explicit distinction between a `Wayfinding Pass` and a later `Prioritisation Pass` (`CONTEXT.md:344-350`);
- the implementation and configuration in `src/satn/`, `config/banes.yaml`, `README.md` and `PRD.md`;
- the checked-in and live GitHub Pages metadata;
- the complete standard test suite;
- the full GitHub issue history, read-only;
- Oxfordshire County Council's [SATN final report](https://mycouncil.oxfordshire.gov.uk/documents/s70847/CMDIDS25042024%20-%20Annex%202%20-%20SATN%20Final%20Report.pdf), [current active-travel page](https://www.oxfordshire.gov.uk/transport-and-travel/local-transport-and-connectivity-plan/active-travel), [initial consultation](https://letstalk.oxfordshire.gov.uk/satn-initial), [final-draft consultation](https://letstalk.oxfordshire.gov.uk/satn), [decision follow-up](https://letstalk.oxfordshire.gov.uk/strategic-active-travel-network-satn-yswd), and [Equalities Impact Assessment](https://mycouncil.oxfordshire.gov.uk/documents/s70849/CMDIDS25042024%2B-%2BAnnex%2B4%2B-%2BEqualities%2BImpact%2BAssessment.pdf);
- the Department for Transport's current published [LCWIP technical guidance and tools](https://www.gov.uk/government/publications/local-cycling-and-walking-infrastructure-plans-technical-guidance-and-tools); and
- the Government's July 2026 [third Cycling and Walking Investment Strategy](https://www.gov.uk/government/publications/the-third-cycling-and-walking-investment-strategy/active-travel-active-england-the-third-cycling-and-walking-investment-strategy-cwis3), which says Active Travel England intends to publish new LCWIP guidance with enhanced walking-network planning by 2027.

There is no `docs/adr/` directory in the current checkout, so there was no system ADR to reconcile.

### Verification state

The standard suite passed on 2026-07-24:

- 235 tests collected;
- 229 passed;
- 6 skipped because live OSM, live agent, browser or live terrain checks are opt-in.

This is strong implementation evidence for the behaviours the tests cover. It is not external validation of route quality, policy compliance, demand, public acceptability, equality impacts or deliverability.

The live `publication.json` and `README.txt` at `https://awjreynolds.github.io/agentic-satn-compiler/` were byte-for-byte identical to the checked-in `site/` files at assessment time. The live result describes itself as:

- schema `2.0`;
- status `reviewable`, not `complete`;
- 75 connections;
- 88 gaps;
- 2,075 urban road-classification unknowns;
- Red connection mandatory checks;
- Red unresolved Access Obligations and School access;
- Red urban School and Community access;
- Grey official-road-classification and elevation-evidence coverage; and
- Grey ATM comparison.

The live `site/README.txt` says, correctly, “Experimental SATN POC — not an adopted B&NES plan.”

## What Oxfordshire's SATN process actually contains

Oxfordshire's SATN is not just a map-generation algorithm. It is a council-led planning process with data analysis, professional judgement, stakeholder engagement, consultation, prioritisation and an approved handoff into feasibility and delivery.

### Baseline and demand

The final report records:

- existing cycling routes and Public Rights of Way;
- Local Plan allocations, LCWIP extents and Greenways;
- National Travel Survey, Census mode share, Strava, the Propensity to Cycle Tool and an “Everyday Trips” model;
- terrain, severance, isochrones, population density, deprivation, public transport and collision data;
- future housing and employment sites; and
- stakeholder and neighbouring-authority input.

The “Everyday Trips” analysis:

1. divided the county into 0.5 km² hexagons;
2. admitted population-weighted centroids and housing sites above 100 dwellings as origins;
3. distinguished higher-order and local destinations;
4. created about 17,000 origin-destination pairs;
5. removed intra-settlement and non-strategic-distance trips;
6. clustered the remaining desire lines in ArcGIS; and
7. combined those results with PCT and Strava evidence.

The method is imperfect. PCT used 2011 travel-to-work data, Strava is a selective sample, and the public report does not fully expose reproducible code, source layers or a scoring workbook. Those weaknesses limit exact replication; they do not make the demand stage optional.

### Network development and prioritisation

Oxfordshire developed and revised a long list of straight desire lines, then:

- split it into segments and subsegments;
- created a SATN Index for settlements and future sites;
- scored existing and future population, workplace population, development, employment, attractors, stations and strategic bus routes;
- summed location scores within 2 km catchments;
- normalised scores per kilometre and to percentages;
- compared subsegment and segment results;
- moderated the results against the existing council delivery pipeline;
- used an officer design workshop to select routes for development; and
- classified links as Strategic/Primary or Complementary/Secondary.

The final classification was not a purely deterministic threshold. Existing council schemes and officer judgement materially influenced it. A replacement process therefore needs an inspectable decision log, not only a formula.

### Engagement, optioneering and handoff

The network changed through early engagement and a July-August 2023 public consultation. The published process then:

- generated potential on-the-ground alignments, often with more than one option;
- classified route typologies;
- produced an illustrative design toolkit;
- retained caveats on feasibility, land, environmental sensitivity and further engagement;
- received formal council approval on 25 April 2024; and
- handed routes into further audit, feasibility, design, landowner engagement, costing and construction.

The report recommends detailed LTN 1/20 audits mindful of the LCWIP Route Selection Tool and Walking Route Audit Tool where networks converge. It also recommends that Oxfordshire *consider* adopting SATN as an LCWIP and create an oversight group. That wording matters: the final project report did not itself prove full LCWIP equivalence.

Oxfordshire's current public pages now call SATN a countywide LCWIP, but they continue to describe town LCWIPs as the within-settlement plans and SATN as the longer-distance connector. The council's Equalities Impact Assessment likewise describes SATN as filling gaps between more detailed plans. The operational model is complementary, not substitutive.

## What `banes-satn` actually implements

The current execution path is clear:

1. `src/satn/sources.py:226-297` creates or validates an immutable, attributable snapshot.
2. `src/satn/pipeline.py:41-99` loads Council Configuration, fingerprints governed inputs, loads the snapshot and optionally constructs a bounded Agent Runtime.
3. `src/satn/compiler.py:117-530` derives Communities, Strategic Spines, a rural Backbone-Outward Assembly, urban spines, Candidate Low-Traffic Areas, School access, School Street assessments, Topography Profiles and Criterion Statuses.
4. `src/satn/publisher.py:35-99` atomically emits GeoPackage, GeoJSON, JSON audit records, a static review map, ZIP and PDF after cross-artifact validation.

The important strengths are real:

- source identity, date, licence and content fingerprints;
- stable identifiers and reproducible compilation;
- explicit unknowns and visible Network Gaps instead of invented straight-line paths;
- authoritative separation between source evidence and generated proposal;
- topology and provenance checks;
- governed official road-classification and elevation-source seams;
- accessible, inspectable HTML alongside map geometry;
- atomic publication and cross-artifact integrity;
- optional ATM-blind comparison; and
- bounded, fingerprinted agent decisions in which the model can only choose compiler-authored actions.

The architecture also declares its limit. `CONTEXT.md:344-350` says demand and accessibility do not determine connections in the Wayfinding Pass and belong to a later Prioritisation Pass. That later pass does not exist in the implementation.

`CouncilConfig` currently contains only source, compilation, ATM and publication configuration (`src/satn/models.py:282-346`). It has no first-class model for:

- LCWIP scope and objectives;
- local policy outcomes;
- demand evidence and travel scenarios;
- engagement or consultation;
- equality duties and impact assessment;
- scheme inventories;
- prioritisation and phasing;
- adoption and approvals;
- delivery ownership; or
- monitoring and update triggers.

The current agent menu is deliberately narrow. Its typed actions are to select a network role, reject a candidate, retain a Network Gap, retain an ATM comparison or terminate (`src/satn/models.py:390-416`). This is a good safety boundary for network compilation. It is not a general-purpose LCWIP reasoning, consultation-synthesis or report-authoring capability.

## Proposition A: Oxfordshire SATN equivalence

### Capability comparison

| Capability | Oxfordshire process | Current B&NES tool | Assessment |
| --- | --- | --- | --- |
| Versioned, attributable evidence | Partial in the published record | Strong immutable snapshot and fingerprints | B&NES is stronger technically. |
| Existing route topology | Baseline GIS layers and professional review | Routable OSM plus governed spine evidence | Partial match; source semantics differ. |
| Demand analysis | NTS, Census, PCT, Strava, Everyday Trips | None | **Missing** |
| Future demand | Housing and employment allocations | Only manually configured Strategic Destinations; no demand model | **Missing** |
| Social and safety context | Population, deprivation, collision and public transport | Amenities and Schools, but no comparable social/safety model | **Missing** |
| Desire-line generation | About 17,000 OD pairs, filtering and clustering | No OD or desire-line pass | **Missing** |
| Strategic scoring | SATN Index, 2 km catchments, per-km normalisation | No equivalent score | **Missing** |
| Prioritisation | Segment/subsegment scoring plus officer moderation | Explicitly deferred; route choice uses cycling-network cost | **Missing** |
| Long-list consultation | Two engagement/consultation rounds changed the network | No consultation workflow | **Missing** |
| Multiple ground alignments | Multiple options retained where applicable | Usually selects one Alignment Option or a Gap | **Material mismatch** |
| Site auditing | Recommended as the next step | No RST, WRAT or LTN 1/20 route audit | **Missing** |
| Design toolkit | Illustrative route/intervention typologies | High-level Intervention Archetypes, but detailed derivation is out of scope | Partial and not equivalent |
| Governance and approval | Steering group, officer workshops, member approval and delivery handoff | Configuration and agent records; no institutional approval workflow | **Missing** |
| Published artefacts | Report, maps and recommendations | Strong machine-readable GIS, review map, ZIP and PDF | Different strengths |

### Architecture mismatch

Oxfordshire starts with demand and policy evidence to decide which connections should form the strategic long list. `banes-satn` starts with A roads, NCN and Greenway evidence, then grows access to the nearest reachable obligations by network cost (`README.md:85-114`, `README.md:372-403`).

Neither approach is inherently invalid, but they answer different questions:

- Oxfordshire: *Which inter-place movements have the strongest strategic case, and which corridors should be prioritised?*
- `banes-satn`: *How can every admitted Access Obligation reach a governed backbone through a continuous, legible topology?*

A network can be topologically coherent but strategically weak. It can connect every Community while ignoring where people travel, who benefits, what future development changes, which gaps suppress trips, or which interventions are deliverable. Conversely, a high-demand desire line can be topologically unresolved. An equivalent process needs both.

### Red-team conclusion on equivalence

The B&NES compiler should not be presented as reimplementing Oxfordshire's SATN. It implements a potentially valuable wayfinding and audit layer that Oxfordshire's public process does not make reproducible. It does not reproduce Oxfordshire's central demand, prioritisation and governance functions.

Issue [#11](https://github.com/awjreynolds/agentic-satn-compiler/issues/11) mapped Oxfordshire into a transferable method, but later product issues narrowed the implementation. Issue [#20](https://github.com/awjreynolds/agentic-satn-compiler/issues/20) explicitly excluded delivery prioritisation, demand/PCT/DfT scoring, costing, engineering feasibility, statutory consultation and adoption. The current `PRD.md:360-376` preserves most of those exclusions. “Issue closed” must not be read as “Oxfordshire equivalence delivered.”

## Proposition B: replacement for an LCWIP

### National LCWIP requirements

The DfT guidance identifies three key outputs:

1. a network plan for walking and cycling with preferred routes and core zones;
2. a prioritised programme of infrastructure improvements for future investment; and
3. a report setting out the underlying analysis and narrative.

Its six stages cover:

1. scope and governance;
2. information and barriers;
3. cycling-network planning;
4. walking-network planning;
5. prioritisation; and
6. integration into policy, strategies and delivery plans.

The guidance distinguishes cycling and walking because their travel patterns and methods differ. It expects Core Walking Zones, key walking routes, route audits, improvement packages, short/medium/long-term priorities, stakeholder involvement, protected-characteristic considerations, policy integration and periodic review. It says LCWIPs should reflect the needs of all and recommends engagement with groups protected under the Equality Act 2010.

### Missing LCWIP outputs

The current publication has no:

- dedicated Walking Network Map;
- Core Walking Zones;
- key walking routes of up to roughly 2 km into those zones;
- Walking Route Audit Tool-compatible assessment;
- demand-led Cycling Network Map;
- Route Selection Tool-compatible assessment of coherent, direct, safe, comfortable and attractive outcomes;
- schedule of specific walking and cycling infrastructure improvements;
- joint prioritised programme;
- short, medium and long-term phasing;
- policy-objective and Local Plan alignment;
- public-engagement and consultation report;
- Equalities Impact Assessment;
- adoption report;
- delivery owners or dependencies;
- monitoring framework; or
- four-to-five-year review/update workflow.

Schools, retail centres and healthcare are visible contextual layers. That is useful, but a layer count is not a walking-network method. Candidate Low-Traffic Areas are useful hypotheses, but they are not audited Core Walking Zones and do not identify the pedestrian improvements necessary to bring a zone to standard.

### “SATN as an LCWIP” does not imply “SATN replaces local LCWIPs”

Oxfordshire's terminology is not a safe shortcut. Its final report says to *consider* adoption as an LCWIP, while its current operational pages continue to distinguish:

- town LCWIPs, which plan local walking and cycling networks; and
- SATN, which connects them over longer distances and fills rural gaps at a less detailed scale.

Even if a council formally adopts a countywide SATN under an LCWIP label, that does not prove that one strategic connector map discharges the walking-network, local-improvement and prioritised-programme functions for Bath, Keynsham, Midsomer Norton, Radstock or other local centres.

### Red-team conclusion on replacement

Do not position SATN as an alternative document to an LCWIP. Position it as one possible strategic-network layer within a family of LCWIP outputs, or as evidence that can refresh part of an existing LCWIP.

The repository's issue history supports this boundary:

- [#1](https://github.com/awjreynolds/agentic-satn-compiler/issues/1), [#20](https://github.com/awjreynolds/agentic-satn-compiler/issues/20) and [#31](https://github.com/awjreynolds/agentic-satn-compiler/issues/31) exclude consultation and adoption;
- [#3](https://github.com/awjreynolds/agentic-satn-compiler/issues/3) defers demand to a future Prioritisation Pass;
- [#16](https://github.com/awjreynolds/agentic-satn-compiler/issues/16) closed the wider Intervention Archetype catalogue as out of scope; and
- no existing issue is specifically scoped to LCWIP outputs, equality, engagement, monitoring, policy integration or a walking-network method.

## Proposition C: an agentic LCWIP foundation

### What is genuinely reusable

The repository contains unusually good foundations for controlled agentic work:

- **Evidence Packet behaviour:** source snapshots are immutable, attributed and fingerprinted.
- **Bounded decisions:** an agent receives a finite choice menu and cannot invent executable actions.
- **Deterministic replay:** accepted choices are tied to dependency fingerprints and compiler-authored actions.
- **Safe failure:** a decision-required or terminated run publishes no partial replacement.
- **Explicit uncertainty:** missing evidence remains Grey or a visible Network Gap.
- **Inspectable outputs:** map features, semantic HTML and audit records share stable identifiers.
- **Council configuration seam:** source and threshold changes do not require a council-specific fork.

These are worth retaining and extending.

### What “agentic” currently means

The live B&NES publication used:

- 161 Agent Decision Records;
- 101 records where no runtime was invoked;
- 60 reviewed records using the deterministic `fake` runtime and model `deterministic-choices-v1`;
- 75 accepts and 86 gaps; and
- no Human Intervention Requests.

That proves the decision protocol executes. It does not prove a model can research, challenge or improve a real LCWIP. The standard suite skips live-agent tests. The current B&NES configuration itself selects `provider: fake` with `response_mode: direct-runtime`.

The absence of Human Intervention Requests alongside 88 visible gaps is not evidence that no human input is needed. The README explains that ordinary no-path or missing-spine gaps do not create intervention requests (`README.md:355-360`). The metric therefore describes a narrow protocol state, not planning readiness.

### Safe agent roles versus unsafe delegation

Agents can safely assist with:

- checking data completeness and provenance;
- regenerating demand and network analyses from governed inputs;
- producing finite alternative menus;
- applying published scoring rules;
- tracing every claim to evidence;
- detecting inconsistent maps, tables and narrative;
- drafting consultation summaries with source-linked dispositions;
- drafting a plan and issue register for human review; and
- rerunning scenario/sensitivity tests.

Agents must not be treated as the accountable decision-maker for:

- selecting policy objectives or hidden weights;
- deciding whose travel needs matter;
- inferring public acceptability from online data;
- resolving protected-characteristic impacts;
- asserting land rights, highway powers or planning status;
- confirming a route is safe or feasible without site evidence;
- disposing of consultation objections without an accountable owner;
- adopting the plan; or
- representing a generated output as council policy.

The correct control model is “agent-prepared, evidence-bound, professionally reviewed and institutionally adopted.”

## False confidence and predictable failure modes

### 1. Internal Green is mistaken for planning quality

Several Criterion Statuses prove internal invariants, not real-world fitness. For example, `authoritative_model`, `legacy_pairwise_absent` and `ncn_kept_as_permeability_evidence` are set Green by construction (`src/satn/compiler.py:327-345`, `src/satn/compiler.py:396-429`). A map dominated by Green internal checks can still lack demand, walking, feasibility and public legitimacy.

Mitigation: never publish one overall “LCWIP compliant” or “plan quality” status. Keep topology, evidence, demand, design, equality, engagement, deliverability and adoption as separate Criteria Sections.

### 2. “Authoritative” is mistaken for “adopted”

Within this repository, authoritative means the current schema's selected compiled network. It does not mean endorsed by B&NES, legally adopted or accepted by affected communities. The repository disclaimer is essential and must survive every export.

Mitigation: reserve `adopted` for a version with a recorded council decision, date, decision URL, scope and supersession status. Rename user-facing “authoritative” language if non-technical audiences could confuse it.

### 3. Backbone completeness is mistaken for demand

Backbone-Outward Assembly can reward proximity to the available spine and construct a clean tree while overlooking high-demand transverse movements. Its anti-mesh rule may also suppress a second useful corridor that demand, resilience or severance evidence would justify.

Mitigation: retain the topology as one candidate network, then overlay OD flows, desire lines, local user evidence and policy outcomes. Permit evidence-backed revisions through governed rules rather than treating tree structure as a policy invariant.

### 4. A-road continuity is mistaken for plausible delivery

An A-road spine is explicitly an assumption that major segregated infrastructure is required. Without land, cross-section, junction, structure, ecology, traffic, maintenance and cost evidence, a visually continuous A-road corridor can be undeliverable.

Mitigation: use a separate feasibility/audit state. Never let indicative intervention coverage satisfy a scheme-quality or deliverability gate.

### 5. OSM completeness is mistaken for official completeness

OSM is valuable for routable geometry, but it is not an authoritative source for highway rights, Public Rights of Way status, land ownership, traffic volumes, footway condition, crossing delay or future development. The live publication's 2,075 road-classification unknowns show the problem is material even with a governed OS Open Roads source.

Mitigation: define a source hierarchy and coverage test for every LCWIP evidence theme. Absence in OSM must remain unknown, not absent.

### 6. Contextual amenities are mistaken for trip demand

The compiler displays Schools, retail centres and healthcare, but it does not model the number, purpose, direction or suppressibility of trips. It omits many LCWIP attractors such as employment intensity, colleges, universities, leisure, bus stops and future development unless manually promoted.

Mitigation: introduce governed origin, destination, demographic and scenario models; retain raw quantities and uncertainty instead of hiding them in a single score.

### 7. Public map availability is mistaken for engagement

Publishing an accessible static map is not consultation. It does not demonstrate representative participation, protected-group engagement, issue disposition, changed proposals or political consent.

Mitigation: add a consultation evidence model with participant privacy controls, spatial comments, themes, affected features, officer response, decision, rationale, version diff and approval.

### 8. Fake-agent execution is mistaken for validated agent judgement

The deterministic fake runtime is excellent for testing the protocol. It supplies no evidence that a production model makes safe or useful planning choices, nor that outputs remain stable across model versions.

Mitigation: create an evaluation corpus with expert-scored decisions, adversarial cases, abstention tests, evidence-citation tests, repeatability measures and provider/model versioning. Production use must be shadowed before it is decision-influencing.

### 9. Reproducibility is mistaken for correctness

A deterministic wrong assumption is still wrong. Fingerprints prove which inputs and rules generated an output; they do not validate those inputs or rules.

Mitigation: add data-quality, external benchmark, sensitivity and professional-review gates. Keep provenance and validity as separate concepts.

### 10. One PDF map is mistaken for an LCWIP report

The current PDF is a map artefact. It does not contain the analysis narrative, separate walking and cycling methods, improvement schedule, prioritisation, engagement outcomes, EqIA, policy integration or update protocol needed for an adoption-ready document.

Mitigation: build a structured plan schema before a document generator. Generate every narrative/table/map from that schema and validate cross-references.

### 11. Oxfordshire is treated as a reproducible gold standard

Oxfordshire's public report is valuable but incomplete as an executable specification. It does not publish all source layers, code, score tables, aggregation rules, manual adjustments or consultation dispositions.

Mitigation: treat Oxfordshire as one process reference, not ground truth. Document every deliberate divergence and validate against DfT/ATE guidance and local professional judgement.

### 12. Current guidance is treated as static

The July 2026 CWIS3 commits ATE to new LCWIP guidance with enhanced walking-network planning by 2027. A hard-coded compliance claim made now may age badly.

Mitigation: version the guidance profile and criteria. Allow one plan to be assessed against more than one guidance version without rewriting historical decisions.

## Gap analysis

### Blocking gaps

These prevent a claim of Oxfordshire equivalence, LCWIP replacement or adoption-ready agentic generation.

#### B1. No LCWIP product and governance contract

There is no schema defining the required plan outputs, accountable roles, stage gates, approval status or distinction between generated, reviewed, consulted and adopted states.

Minimum acceptance evidence:

- versioned LCWIP scope and objectives;
- named Senior Responsible Owner, Project Board and accountable plan owner;
- explicit state machine from evidence collection through adoption and refresh;
- immutable approval records and links to council decisions;
- persistent disclaimer until adoption; and
- prohibition on agent self-approval.

Related existing issues: [#1](https://github.com/awjreynolds/agentic-satn-compiler/issues/1), [#5](https://github.com/awjreynolds/agentic-satn-compiler/issues/5), [#20](https://github.com/awjreynolds/agentic-satn-compiler/issues/20), [#59](https://github.com/awjreynolds/agentic-satn-compiler/issues/59).

#### B2. No governed demand, origin-destination or desire-line pass

The compiler cannot reproduce Oxfordshire's central analytical stage or DfT cycling-network planning.

Minimum acceptance evidence:

- current and future origins/destinations with source dates and uncertainty;
- Census/PCT or successor scenarios;
- public transport, employment, education, healthcare, retail, leisure and development inputs;
- transparent OD construction and distance rules;
- desire-line clustering/aggregation with sensitivity tests;
- raw quantities preserved alongside any classifications; and
- comparison fixtures against published or expert-reviewed networks.

Related existing issues: [#3](https://github.com/awjreynolds/agentic-satn-compiler/issues/3), [#11](https://github.com/awjreynolds/agentic-satn-compiler/issues/11), [#14](https://github.com/awjreynolds/agentic-satn-compiler/issues/14), [#20](https://github.com/awjreynolds/agentic-satn-compiler/issues/20).

#### B3. No dedicated walking-network method

Walking is present in language and some intervention assumptions, but not as an LCWIP Walking Network Map or programme.

Minimum acceptance evidence:

- governed walking trip generators;
- Core Walking Zones;
- key walking routes and funnel/severance routes;
- footway, crossing, accessibility, personal-security and maintenance evidence;
- WRAT-compatible route/zone audits;
- walking-specific infrastructure improvements; and
- separate walking criteria that cannot be satisfied by cycling topology.

No current GitHub issue owns this scope.

#### B4. No route/site audit and feasibility boundary

The compiler selects indicative alignment geometry without an RST, WRAT, LTN 1/20 or equivalent site-audit workflow. It cannot establish that preferred routes can reach an acceptable standard.

Minimum acceptance evidence:

- route alternatives retained and compared;
- coherent/direct/safe/comfortable/attractive audit evidence;
- traffic speed/volume, junction, crossing, width, surface, lighting, access control and maintenance evidence;
- land, legal status, structures, ecology, flood and planning constraints;
- site-survey date, author and confidence;
- explicit “not audited”, “not feasible” and “requires design” states; and
- no inference from map geometry alone.

Related existing issues: [#7](https://github.com/awjreynolds/agentic-satn-compiler/issues/7), [#15](https://github.com/awjreynolds/agentic-satn-compiler/issues/15), [#16](https://github.com/awjreynolds/agentic-satn-compiler/issues/16), [#31](https://github.com/awjreynolds/agentic-satn-compiler/issues/31).

#### B5. No scheme/improvement register or joint prioritised programme

An LCWIP prioritises infrastructure improvements, not merely corridors or topology.

Minimum acceptance evidence:

- each deficiency linked to one or more candidate improvements;
- coherent route/zone packages;
- effectiveness, policy and deliverability criteria;
- public acceptability and environmental constraints;
- current and forecast beneficiaries;
- dependencies, owners and indicative costs/confidence;
- short, medium and long-term programme; and
- transparent human-approved weights, overrides and sensitivity analysis.

Related existing issues: [#11](https://github.com/awjreynolds/agentic-satn-compiler/issues/11), [#16](https://github.com/awjreynolds/agentic-satn-compiler/issues/16), [#17](https://github.com/awjreynolds/agentic-satn-compiler/issues/17), [#20](https://github.com/awjreynolds/agentic-satn-compiler/issues/20).

#### B6. No engagement, consultation, equality or public-sector-duty workflow

The current product cannot demonstrate that affected people shaped the network or that protected-characteristic impacts were considered.

Minimum acceptance evidence:

- stakeholder map and engagement plan;
- accessible consultation materials and channels;
- demographic/representation monitoring with privacy controls;
- spatial and thematic response capture;
- issue-by-issue disposition and network version diff;
- Equality Impact Assessment inputs, mitigations and accountable sign-off;
- records of disagreement and unresolved concerns; and
- human decision gates at consultation launch and closure.

No current GitHub issue owns this scope. Existing product issues explicitly exclude consultation.

#### B7. Current B&NES output is not a valid replacement baseline

The live output is `reviewable`, with 88 gaps and material Red/Grey sections. ATM comparison is disabled/grey. It has not been approved by B&NES.

Minimum acceptance evidence:

- an agreed validation corpus, including the B&NES Active Travel Masterplan where licensing permits;
- expert review of sampled rural, urban, School and cross-boundary outputs;
- every Red and material Grey dispositioned;
- known omissions and commissionable evidence requests;
- reproducible comparison against the prior plan without treating either as ground truth; and
- a council decision on whether the network is suitable for consultation, not adoption.

Related existing issues: [#27](https://github.com/awjreynolds/agentic-satn-compiler/issues/27), [#29](https://github.com/awjreynolds/agentic-satn-compiler/issues/29), [#30](https://github.com/awjreynolds/agentic-satn-compiler/issues/30), [#43](https://github.com/awjreynolds/agentic-satn-compiler/issues/43).

### Issue-shaping recommendation

Do not file the gaps as seven unrelated implementation tickets and do not put the whole
LCWIP ambition into one unbounded issue. Create one parent PRD for an **LCWIP preparation
system around the existing SATN compiler**, then make B1-B7 explicit child workstreams.

The dependency order should be:

1. B1 defines the product, plan schema, human authority and stage gates.
2. B2 and B3 implement separate cycling-demand and walking-network planning contracts.
3. B4 turns candidate corridors, routes and zones into auditable deficiencies and options.
4. B5 consumes B2-B4 to create the improvement register and prioritised programme.
5. B6 is a cross-cutting governance stream that must shape B1-B5, not a final consultation
   feature bolted onto a finished network.
6. B7 validates the current B&NES output and supplies a regression/evaluation corpus; it
   must not define the portable rules.

Each child issue should state:

- the LCWIP/DfT/ATE outcome it satisfies;
- governed input and output schemas;
- what remains a human or professional judgement;
- explicit non-goals, especially adoption and detailed design;
- privacy, licence, equality and evidence-coverage constraints;
- deterministic and expert-reviewed acceptance fixtures;
- publication and provenance requirements; and
- blocking relationships to the other workstreams.

B1 and the professional-policy choices within B4-B6 require accountable human ownership.
Only implementation slices with accepted schemas, bounded decisions and testable acceptance
criteria should receive `ready-for-agent`. This prevents an agent from silently making the
policy choices that the product contract is meant to expose.

### Major gaps

These may not block a research prototype, but they block a credible operational LCWIP preparation service.

#### M1. Policy and land-use integration

Add governed Local Transport Plan, Local Plan, development allocation, Rights of Way Improvement Plan, road-scheme, maintenance, public-health and neighbouring-authority evidence. Record conflicts and how each proposed improvement supports or conflicts with policies.

#### M2. Social, safety and distributional evidence

Add population, deprivation, car availability, disability/accessibility, collision, air quality, health and rural isolation evidence. Avoid a composite score that conceals who benefits or loses.

#### M3. Official Public Rights of Way and legal-status evidence

OSM path tags are not a substitute for the definitive map, highway records, permissive agreements or landowner consent. Introduce official source adapters and explicit legal/land unknowns.

#### M4. Consultation change control

The publication pipeline is immutable and reviewable, but there is no governed workflow for proposing, accepting and rejecting changes in response to people or partner organisations. Extend the decision ledger rather than permitting manual GIS edits.

#### M5. Adoption-ready report generation

Define a structured plan model and generate:

- executive summary;
- scope, governance and objectives;
- baseline and methods;
- cycling and walking network plans;
- audit findings and infrastructure programme;
- prioritisation and phasing;
- engagement/consultation outcomes;
- equality and policy integration;
- delivery, monitoring and review; and
- complete source/data appendices.

The present map PDF must remain a map annex, not be relabelled as the plan.

#### M6. Agent evaluation and model governance

Create an expert-reviewed benchmark, adversarial test suite, evidence-citation checks, abstention expectations, provider/model change controls, token/cost records, and a shadow-mode release gate. A fake-runtime pass is protocol verification only.

#### M7. Criteria semantics and external assurance

Separate:

- code/integrity checks;
- source coverage;
- planning evidence;
- route quality;
- equality;
- public legitimacy;
- deliverability; and
- adoption.

Publish definitions beside every status. Commission independent transport-planning review before a public LCWIP claim.

#### M8. Monitoring, refresh and supersession

The DfT guidance envisages LCWIP review about every four to five years and after significant policy, development or funding changes. Add:

- dataset refresh policies;
- plan review triggers;
- delivery/progress measures;
- before/after monitoring;
- supersession links; and
- reproducible change reports.

#### M9. Cross-boundary and delivery ownership

Cross-Boundary Gateways identify geometry, but not institutional responsibility, onward-network status, joint funding, or neighbouring-authority agreement. Add party, contact, approval, dependency and unresolved-boundary records.

#### M10. Tracker truth and capability claims

Open parent issues [#31](https://github.com/awjreynolds/agentic-satn-compiler/issues/31) and [#59](https://github.com/awjreynolds/agentic-satn-compiler/issues/59) remain open after their listed child slices closed. Issues [#76](https://github.com/awjreynolds/agentic-satn-compiler/issues/76), [#77](https://github.com/awjreynolds/agentic-satn-compiler/issues/77) and [#80](https://github.com/awjreynolds/agentic-satn-compiler/issues/80) also remain open although later delivery issues claim corresponding work. Several closed issues retain unchecked acceptance boxes.

Issue state should not drive public capability claims. Add an evidence-backed capability matrix tied to tests, code, publication schema and a release/version.

### Useful enhancements

These improve usability and assurance after the blocking and major gaps have owners.

1. Publish a machine-readable data dictionary for every GeoPackage/GeoJSON layer and field.
2. Add scenario comparison for demand assumptions, prioritisation weights and future development.
3. Show evidence age, coverage and uncertainty spatially.
4. Generate Evidence Requests directly from material Grey states.
5. Add a structured red-team report to every candidate plan version.
6. Provide route/zone comment exports that can be imported into common consultation platforms without personal data.
7. Add a professional-review checklist and sign-off dashboard.
8. Provide Local Plan/development-management exports for safeguarding alignments and assessing planning applications.
9. Add delivery-progress and supersession views without mutating the adopted baseline.
10. Test representative screen-reader, keyboard, mobile, print and offline workflows across the complete LCWIP document set, not only the review map.

## Recommended product shape

Do not expand one compiler until it becomes an opaque “LCWIP generator.” Preserve deep modules and explicit boundaries.

```text
Governed evidence registry
    ├── policy, population, development, demand, safety, equality
    ├── official network, PRoW, terrain, land/legal, surveys
    └── engagement and consultation evidence
            ↓
Demand and desire-line analysis
            ↓
SATN wayfinding/compiler (this repository's core strength)
            ↓
Cycling route selection + walking zone/route planning
            ↓
Audit and improvement-package register
            ↓
Prioritisation and delivery programme
            ↓
Consultation disposition + equality/policy assurance
            ↓
Structured LCWIP plan and adoption pack
            ↓
Human governance decision
```

The SATN compiler should be allowed to emit a candidate, a Gap, a Challenge Finding or an Evidence Request. It should not be required to manufacture an answer so that document generation can finish.

## Minimum safe foundation

Before calling the system an “agentic LCWIP foundation” in a product or funding context, require all of the following:

1. **Product boundary:** written distinction between strategic network generation, LCWIP preparation and formal adoption.
2. **Guidance profile:** versioned DfT/ATE and local criteria, with planned migration for the 2027 guidance update.
3. **Governed evidence:** demand, future development, policy, social/safety, official network, PRoW, land/legal and survey sources with coverage and refresh rules.
4. **Separate modal methods:** cycling demand/route selection and walking Core Walking Zone/key-route planning.
5. **Audited improvements:** route/zone deficiencies and evidence-backed intervention packages, not generic corridor labels.
6. **Transparent prioritisation:** effectiveness, policy, equality and deliverability evidence; short/medium/long-term programme; human-approved trade-offs.
7. **Public legitimacy:** engagement, consultation disposition, accessible participation and Equalities Impact Assessment.
8. **Agent controls:** bounded roles, citations, abstention, evaluation corpus, model/version provenance, shadow testing and mandatory human gates.
9. **Plan schema:** one structured source for maps, tables, narrative, appendices and adoption records.
10. **External validation:** transport-planner review, council-owner review and sampled site verification before consultation; formal council decision before adoption.

## Final go/no-go

- **No-go** on claiming functional equivalence with Oxfordshire's SATN today.
- **No-go** on using the generated SATN as a replacement for an LCWIP.
- **No-go** on autonomous generation of an adoption-ready LCWIP.
- **Conditional go** on using the repository as the geospatial evidence, wayfinding, audit and controlled-agent substrate for a wider LCWIP preparation system.

The recommended way forward is to preserve the current compiler's strong evidence and safety contracts, then build the missing LCWIP stages around it. The first programme increment should not be “write an LCWIP document.” It should be a versioned LCWIP product contract plus the governed demand/desire-line and walking-network modules. Until those exist, a polished generated report would mainly automate the appearance of completeness.
