# Can the B&NES SATN tool replace Oxfordshire's process or generate an LCWIP?

- Assessment date: 24 July 2026
- Repository assessed: `awjreynolds/agentic-satn-compiler` at `e38901e`
- Scope: functional equivalence, replacement readiness, LCWIP conformance, and agentic
  foundation
- Status: technical and product assessment, not legal advice or a council adoption decision

## Executive answer

### Decisions

| Question | Answer | Confidence |
| --- | --- | --- |
| Does the current B&NES tool deliver functionality equivalent to Oxfordshire's SATN process? | **No.** It implements a different and narrower Wayfinding Pass. It is stronger in deterministic topology, routability, provenance, reproducible publication and bounded agent control, but omits Oxfordshire's multi-source demand baseline, desire-line model, strategic scoring and prioritisation, consultation-led revision, and full optioneering discipline. | High |
| Could it replace the Oxfordshire process today? | **No.** It could replace part of the technical network-compilation work, but not the whole analytical and governance process. | High |
| Could the current SATN output replace an LCWIP? | **No.** It provides only part of a cycling network plan. It does not provide a walking and wheeling network plan, a prioritised programme of infrastructure improvements, or the supporting LCWIP analysis, engagement and policy-integration record. | High |
| Is it a useful foundation for producing an LCWIP agentically? | **Yes, conditionally.** Keep it as the deterministic strategic cycling-network kernel inside a broader LCWIP workspace. Do not turn `satn.compile()` into an unconstrained plan-writing agent. | High |
| Could a future SATN be adopted instead of a separately branded LCWIP? | **Possibly as a local governance choice, but only after it delivers LCWIP-equivalent functions and is taken through the council's evidence, engagement, equality, policy and adoption process.** Calling it a SATN or a “countywide LCWIP” does not remove those requirements. | Medium-high |

The recommended product direction is therefore:

> **SATN-assisted LCWIP, not SATN-as-LCWIP.**

Retain the present compiler's deep module and safety properties. Add a separate, staged
LCWIP layer for scope and governance, wider evidence, cycling demand, walking and wheeling,
route and area audit, infrastructure interventions, prioritisation, engagement, report
assembly, release history and monitoring. The tool may generate an inspectable
**LCWIP adoption candidate**. Only an authorised council process can turn that candidate
into an adopted plan.

## Why Oxfordshire does not establish a simple replacement precedent

Oxfordshire County Council describes its SATN as a long-term countywide network connecting
town LCWIPs and covering the strategic or rural space between settlements. Its own initial
consultation explicitly says place-based LCWIPs and SATN take different approaches, with
SATN working at a less detailed inter-settlement scale:
[OCC initial SATN consultation](https://letstalk.oxfordshire.gov.uk/satn-initial).

Oxfordshire sometimes now describes SATN as a “countywide LCWIP” or “super LCWIP”, but its
delivery practice keeps the layers distinct:

- SATN connects local LCWIP networks and concentrates on inter-urban links
  ([OCC SATN overview](https://letstalk.oxfordshire.gov.uk/strategic-active-travel-network-satn-yswd));
- the 2026 Wallingford LCWIP records which local routes pick up SATN links, demonstrating
  integration rather than substitution
  ([Wallingford Area draft LCWIP](https://mycouncil.oxfordshire.gov.uk/documents/s81647/CMDTM23042026%20-%20Wallingford%20LCWIP%20Draft%20LCWIP.pdf));
- Oxfordshire continues to maintain and develop multiple place-based LCWIPs
  ([OCC active travel plans](https://www.oxfordshire.gov.uk/transport-and-travel/local-transport-and-connectivity-plan/active-travel)); and
- its 2025 LCWIP prioritisation procedure uses SATN mapping as one evidence source alongside
  other criteria, rather than treating SATN as the complete LCWIP process
  ([OCC Cabinet response, September 2025](https://mycouncil.oxfordshire.gov.uk/documents/g7808/Public%20reports%20pack%20Tuesday%2016-Sep-2025%2014.00%20Cabinet.pdf?T=10)).

The practical precedent is a two-scale planning system:

1. a strategic, countywide/inter-urban network; and
2. more detailed local walking, wheeling and cycling plans and intervention programmes.

That is compatible with using B&NES SATN as an LCWIP input. It is not evidence that the
present output is itself an LCWIP.

## Baseline: what Oxfordshire's SATN process delivered

Oxfordshire's March 2024 Stage 1 method had four substantive technical stages:

1. **Baseline analysis.** It combined existing routes and public rights of way, planned
   development and policies, LCWIPs and Greenways, travel and demand sources, population,
   deprivation, public transport, collisions, terrain and severance.
2. **Network development.** It generated and consulted on a long list of straight demand
   lines, then organised them into longer segments and sub-segments.
3. **Network prioritisation.** It calculated a settlement/location index, aggregated it
   around corridors, normalised it by length, reviewed results against the delivery
   pipeline and classified primary and complementary links.
4. **Route optioneering.** It developed multiple indicative physical alignments and
   typologies for priority lines, while explicitly reserving feasibility, design,
   landowner engagement and costing for later work.

Primary sources:

- [OCC SATN Project Report, March 2024](https://mycouncil.oxfordshire.gov.uk/documents/s70847/CMDIDS25042024%20-%20Annex%202%20-%20SATN%20Final%20Report.pdf)
- [OCC decision report, 25 April 2024](https://mycouncil.oxfordshire.gov.uk/documents/s70845/CMDIDS25042024%20-%20Strategic%20Active%20Travel%20Network%20Stage%201.pdf)
- [OCC initial consultation](https://letstalk.oxfordshire.gov.uk/satn-initial)
- [OCC final consultation](https://letstalk.oxfordshire.gov.uk/strategic-active-travel-network-satn-yswd)

The prior repository research correctly identified the transferable discipline as:
evidence → candidate network → transparent prioritisation → indicative alignments →
later feasibility. The current implementation has intentionally implemented only part of
that sequence.

## What the B&NES tool actually is

The repository defines a council-portable Python compiler whose current authoritative model
is a cycling-led **Backbone-and-Access Network**. It:

- snapshots governed OSM, National Cycle Network, official road-classification and elevation
  evidence;
- derives Communities, Schools, Cross-Boundary Gateways, Strategic Spines, Urban
  Main-Road Spines, Candidate Low-Traffic Areas and portals;
- grows rural access branches outward from A-road and NCN spines;
- identifies first-meeting Cross-Spine Connectors;
- assesses rural and urban School Access Obligations and School Street candidates;
- compares topography-triggered alignment alternatives;
- exposes Network Gaps, criterion states and provenance;
- uses stable, fingerprinted, compiler-authored Agent Decision Requests;
- publishes GeoPackage, GeoJSON, JSON records, an accessible static review map, ZIP and
  PDF atomically.

The core implementation evidence is:

- council/source configuration:
  [`models.py`](../../src/satn/models.py#L143-L340);
- compiled network shape:
  [`compiler.py`](../../src/satn/compiler.py#L62-L116);
- source snapshot:
  [`sources.py`](../../src/satn/sources.py#L226-L299);
- compilation pipeline and input fingerprint:
  [`pipeline.py`](../../src/satn/pipeline.py#L41-L608),
  [`pipeline.py`](../../src/satn/pipeline.py#L700-L738);
- bounded decision protocol:
  [`agents.py`](../../src/satn/agents.py#L314-L617);
- atomic, cross-validated publication:
  [`publisher.py`](../../src/satn/publisher.py#L51-L101).

These are valuable foundations. They should not be understated merely because the tool is
not an LCWIP generator.

The repository also makes the current product boundary explicit:

- route choice is separate from later demand or delivery prioritisation
  ([README](../../README.md#L10-L14));
- the Wayfinding Pass excludes demand and accessibility from connection choice, and the
  Prioritisation Pass is defined as later work
  ([CONTEXT](../../CONTEXT.md#L343-L349));
- the public output is an experimental generated network, not an adopted plan, and still
  requires scheme-level feasibility and design
  ([README](../../README.md#L533-L539)).

## Functional comparison with Oxfordshire SATN

Legend: **Delivered**, **Partial**, **Absent**, or **Different**.

| Capability | Oxfordshire Stage 1 | Current B&NES tool | Assessment |
| --- | --- | --- | --- |
| Governed study boundary | Countywide scope including cross-boundary context | Council Configuration, governed boundary and named gateways | **Delivered**, with stronger reproducibility |
| Multi-source baseline | Travel demand, census/NTS/PCT/Strava, population, deprivation, collisions, public transport, development, policies, existing routes, PRoW, terrain | OSM cycling network and places, NCN, amenities, council road classification, optional through-traffic and elevation | **Partial**; the transport/planning/demographic baseline is largely missing |
| Existing and future trip patterns | Multiple observed and modelled demand sources | No trip model; amenity profiles are deliberately qualitative | **Absent** |
| Origin-destination candidate generation | About 17,000 OD candidates before filtering and clustering | Communities and Schools are Access Obligations attached to a strategic backbone | **Different**; useful topology, not demand-line equivalence |
| Long-list desire lines | Consulted long list between strategic focal points | No published long-list desire-line corpus or consultation lineage | **Absent** |
| Segments and sub-segments | Both used to balance strategic continuity and local opportunity | Spine/access/meeting edge types exist, but not Oxfordshire's demand-scored segmentation | **Partial/different** |
| Topology and routability | Strategic lines and indicative route options; no equivalent deterministic contract is evident in the public report | Continuous bidirectional graph routing, stable attachments, gaps, gateway and topology checks | **Delivered more strongly** |
| Strategic scoring | SATN Index, corridor catchments, per-km normalisation, review and primary/complementary classes | No demand or delivery score by design; traffic lights assess validity, not priority | **Absent** |
| Sensitivity and transparent assumptions | Public method exposes many choices but not a fully reproducible formula | Configuration and fingerprints are strong, but there is no priority model to test | **Absent for prioritisation** |
| Multiple indicative route options | Officers/stakeholders considered multiple alignments and typologies | Deterministic route candidates and topography alternatives; one authoritative selected route | **Partial** |
| Existing/future route-quality audit | Route types, constraints and later feasibility boundary | Topography, classification, permeability, access and continuity evidence; no complete route-quality/RST or design-outcome audit | **Partial** |
| Intervention/toolkit output | Non-prescriptive typology toolkit and constraints | Broad intervention assumptions/archetypes, not an intervention catalogue or route improvement programme | **Partial** |
| Consultation and local knowledge | Early and final public consultation, steering group and officer/stakeholder workshops | No consultation, stakeholder register or representation-handling workflow | **Absent** |
| Governance/adoption | Officer review and Cabinet Member decision linked to the LTCP | Bounded machine decisions and audit records; explicit non-adopted disclaimer | **Different**; technical governance cannot replace democratic authority |
| Review publication | Maps, technical report, consultation and decision record | Strong machine-readable, GIS, accessible map and PDF bundle | **Delivered technically**, but missing the plan narrative and decision record |

### Oxfordshire replacement verdict

The B&NES tool can credibly replace or improve:

- governed source capture for the sources it supports;
- routable alignment generation;
- network topology validation;
- explicit unknown/gap handling;
- topographic evidence;
- machine-readable provenance;
- reproducible review-map publication; and
- bounded, auditable agent decision control.

It cannot replace:

- the broad baseline;
- the demand/desire-line model;
- strategic and delivery prioritisation;
- the full route/constraint/typology optioneering process;
- consultation and local knowledge;
- policy integration; or
- council approval.

It is therefore a **partial technical replacement and a different network hypothesis**, not
a process-equivalent replacement.

## LCWIP conformance assessment

The currently published DfT technical guidance identifies three key LCWIP outputs:

1. a walking and cycling network plan identifying preferred routes and core zones;
2. a prioritised programme of infrastructure improvements; and
3. a report containing the underlying analysis and supporting narrative.

Source:
[DfT LCWIP technical guidance, paragraphs 2.1–2.8](https://assets.publishing.service.gov.uk/media/5f32aa668fa8f57ac88dc9dc/cycling-walking-infrastructure-technical-guidance-document.pdf).

The guidance uses six stages. The 2026 statutory Local Transport Plan guidance repeats the
substance—information gathering, walking/wheeling/cycling network planning, a prioritised
10-year pipeline, and broad stakeholder engagement—and says LCWIPs should align with the
LTP's active-travel policies:
[DfT Local Transport Plan guidance, active travel section](https://www.gov.uk/government/publications/local-transport-plans/local-transport-plans).

| LCWIP stage | Expected function | Current support | Verdict |
| --- | --- | --- | --- |
| 1. Determining scope | Geography, objectives, governance, delivery arrangements, engagement and timeframe | Council boundary and source/configuration scope; no SRO/project board, objectives/targets, engagement plan, equality plan or adopted timeframe | **Partial, not conformant** |
| 2. Gathering information | Existing and potential walking/cycling trips, barriers, existing conditions, land-use and transport policies/programmes | Strong governed capture for a narrow network evidence family; most demand, demographic, collision, public-transport, development, PRoW, policy and existing-condition evidence absent | **Partial, not conformant** |
| 3. Network planning for cycling | OD points/flows, desire lines, preferred routes, route selection/audit, required improvements and network density | Strong strategic topology and indicative routing; no flow-led desire-line pass, complete route-selection audit, existing/future design-outcome comparison or improvement programme | **Useful kernel, not complete** |
| 4. Network planning for walking | Trip attractors, Core Walking Zones, key/funnel routes, walking audits, vulnerable-user needs and improvements | Candidate Low-Traffic Areas and School access are useful context but do not constitute a walking/wheeling plan or audit | **Absent** |
| 5. Prioritising improvements | Joint walking/cycling programme, short/medium/long-term phasing, effectiveness, policy, deliverability, costs and appraisal | No Prioritisation Pass, cost model, infrastructure programme or phasing | **Absent** |
| 6. Integration and application | Link to LTP/local plan and delivery plans, funding/business cases, development management, review/update | Static experimental publication; no policy mapping, adoption pack, funding/appraisal pack, monitoring or plan revision lifecycle | **Absent** |

### Core-output verdict

| Required output | Current output |
| --- | --- |
| Walking and cycling network plan | Partial strategic cycling network only; no walking/wheeling network and no complete preferred-route audit |
| Prioritised infrastructure programme | Absent |
| Analysis and supporting plan narrative | Method README and machine records, but no LCWIP report, consultation/equality record or policy narrative |

The current tool therefore cannot claim LCWIP equivalence.

## Current B&NES publication readiness

Even the strategic network itself is presently a review artifact rather than a completed
authority-wide answer. The checked-in publication records:

- status `reviewable`;
- 75 compiled connections;
- 88 Network Gaps;
- Red `all_access_obligations_resolved`;
- Red urban School and Community access criteria; and
- Grey official-road-classification and elevation-coverage criteria.

Source:
[`site/publication.json`](../../site/publication.json#L1-L80).

This is healthy evidence behaviour—the compiler exposes uncertainty rather than hiding it.
It is also a decisive reason not to present the current map as an adopted network or LCWIP.

## Red-team challenge

### Blocking gaps

1. **Mode and scale mismatch.** SATN is cycling-led and strategic. LCWIP requires walking
   and wheeling as first-class networks, including detailed urban/core-zone work.
2. **No infrastructure programme.** A line, spine, Candidate Low-Traffic Area or Network Gap
   is not a scoped infrastructure improvement. Without interventions, costs and phasing,
   there is nothing to prioritise as an LCWIP programme.
3. **No demand and benefit case.** The compiler intentionally excludes demand from
   wayfinding. That is defensible for topology but cannot support LCWIP impact,
   prioritisation or investment claims.
4. **No engagement or human authority chain.** Model review cannot stand in for local
   knowledge, stakeholder engagement, equality work, officer accountability or member
   adoption.
5. **No LCWIP report/conformance artifact.** GIS and audit JSON are necessary but do not
   constitute the plan, narrative or policy-integration record.
6. **The current network is reviewable and contains blocking gaps.** Its own criteria
   prevent a completion claim.

### Major gaps

1. Existing-condition route and area audit, including accessibility and personal-safety
   evidence.
2. Walking/wheeling trip attractors, Core Walking Zones, key routes and funnel routes.
3. Cycling OD flows and explicit desire-line/network-density evidence.
4. A governed intervention and constraint model covering route sections, junctions,
   crossings, area measures, supporting infrastructure and maintenance.
5. Conceptual scope, cost ranges, land/rights, environmental constraints, dependency and
   delivery readiness.
6. Transparent prioritisation with sensitivity analysis and separate views of
   effectiveness, policy and deliverability.
7. Local Plan, LTP, public-health, carbon, safety, accessibility and funding alignment.
8. Versioned engagement evidence and disposition of representations.
9. Plan release history, implementation tracking, monitoring and scheduled review.

### False-confidence traps

- **“Backbone-outward order” is not delivery priority.** It is a deterministic assembly
  strategy and must never be labelled a funded or benefit-ranked programme.
- **“Evidence-led” is not “complete evidence”.** The evidence in the snapshot is well
  governed, but its coverage is deliberately narrow.
- **A stable route is not a feasible scheme.** OSM continuity says little about land,
  detailed design, traffic, crossings, cost or public acceptability.
- **A bounded model choice is not a consultation result.** The Agent Runtime can select a
  compiler-authored option; it cannot manufacture public consent or democratic authority.
- **Traffic lights are not priority scores.** They report criterion states. Reusing them as
  a ranking would violate the repository's domain model.
- **A generated PDF map is not a plan document.**
- **“Council-portable” has not yet been proven by a second council.** Configuration
  separation is promising, but real portability requires a second deployment and evidence
  adapter exercise.

### Guidance-change risk

As of this assessment, the 2017 LCWIP technical guidance remains the published technical
method. The June 2026 third Cycling and Walking Investment Strategy commits to new LCWIP
guidance with enhanced walking network planning and LTP integration by 2027:
[CWIS3](https://www.gov.uk/government/publications/the-third-cycling-and-walking-investment-strategy/active-travel-active-england-the-third-cycling-and-walking-investment-strategy-cwis3).

The architecture must therefore version a **Guidance Profile** and conformance matrix. It
must not hard-code the 2017 RST/WRAT spreadsheets as timeless truth.

## Recommended product architecture

### Keep the SATN compiler deep and narrow

Do not add every LCWIP concern to `satn.compile()`. Preserve its present contract:

```text
governed network evidence
        ↓
deterministic SATN Wayfinding Pass
        ↓
reviewable strategic cycling network + gaps + evidence
```

Build a separate LCWIP application/service layer:

```text
Guidance Profile + Council Plan Configuration
        ↓
Scope and Governance Gate
        ↓
Governed Multi-source Evidence Registry
        ├── SATN Wayfinding Pass
        ├── Cycling Demand and Desire-line Pass
        └── Walking and Wheeling Network Pass
        ↓
Route / Area Audit and Intervention Compiler
        ↓
Prioritisation and Scenario Pass
        ↓
Engagement, Equality and Policy Integration Gates
        ↓
LCWIP Adoption Candidate + Conformance Manifest
        ↓
External council decision and signed adoption record
```

The existing `satn` package should remain independently runnable. A new `lcwip` package can
depend on its published models and outputs rather than reaching into private functions.

### Required bounded contexts

1. **Guidance and conformance**
   - versioned DfT/ATE Guidance Profile;
   - required/recommended output matrix;
   - migration between guidance versions;
   - machine-readable conformance result with evidence links.

2. **Scope and governance**
   - study areas and plan horizon;
   - objectives, targets and policy references;
   - SRO/project board and decision authorities;
   - engagement, equality and review strategy;
   - explicit plan status and permitted claims.

3. **Evidence registry**
   - raw, derived, observed, modelled and stakeholder evidence roles;
   - source, licence, date, geographic/temporal coverage, bias and quality;
   - adapters for demographics, travel flows, PCT/scenarios, collisions, public
     transport, development, policy, PRoW, existing infrastructure and local datasets;
   - immutable snapshots and Evidence Requests.

4. **Cycling planning**
   - OD flows and desire lines;
   - strategic/local network reconciliation;
   - SATN output as one network hypothesis;
   - route alternatives, network density and route-quality audits;
   - existing and potential design-outcome states.

5. **Walking and wheeling planning**
   - trip attractors and catchments;
   - Core Walking Zones;
   - key and funnel routes;
   - route/area audits and accessibility evidence;
   - packages of walking, wheeling and public-realm improvements.

6. **Interventions and constraints**
   - section, junction, crossing, area and supporting-infrastructure interventions;
   - current deficiency, proposed outcome and evidence;
   - conceptual scope/cost range, maintenance, land/rights, dependencies, environmental
     and delivery uncertainty;
   - explicit boundary between strategic option and scheme design.

7. **Prioritisation**
   - council-governed criteria and transforms;
   - separate effectiveness, policy and deliverability sections;
   - short/medium/long-term programme;
   - sensitivity/scenario results and missing-data effects;
   - optional appraisal adapters without hidden composite truth.

8. **Engagement, equality and human authority**
   - stakeholder register and engagement events;
   - immutable representations with privacy controls;
   - claim/evidence extraction and human-verified dispositions;
   - protected-characteristic/accessibility review;
   - named human gates and Governance Directives;
   - adoption only from an external authorised decision record.

9. **Publication and monitoring**
   - plan report, maps, intervention/programme tables and appendices;
   - citations and conformance manifest;
   - release archive and comparisons;
   - delivery status, monitoring indicators and scheduled review;
   - watermarked lifecycle states.

### Safe agentic operating model

Use agents around deterministic tools, not in place of them.

| Role | Permitted contribution | Not permitted |
| --- | --- | --- |
| Evidence Steward | Classify sources, identify coverage/bias, create Evidence Requests | Invent missing data or silently change evidence |
| Cycling Analyst | Propose typed desire lines/routes from governed flow evidence | Directly publish or waive topology/design failures |
| Walking and Accessibility Analyst | Propose zones/routes/audit findings | Claim a site condition not supported by survey evidence |
| Intervention Analyst | Propose compiler-catalogued measures and constraints | Produce detailed engineering design |
| Prioritisation Analyst | Explain scenarios and sensitivity | Choose hidden weights or present a score as fact |
| Engagement Synthesiser | Group and summarise governed representations with citations | Fabricate consultation, consent or demographic representativeness |
| Evidence Critic | Challenge provenance, missing data and unsupported claims | Alter source evidence |
| Network/Design Red Team | Challenge coverage, safety, accessibility and deliverability | Relabel Red as Green |
| Report Drafter | Assemble narrative from accepted structured records | Create uncited plan commitments |

The present Agent Decision Request pattern—fingerprinted context, finite choices, compiler
actions, hard deadlines, replay and invariant checks—is the correct foundation. Its current
action vocabulary is route-specific, so LCWIP needs domain-specific typed actions or a
general decision-envelope protocol. The controlling agent should orchestrate fresh
compilations and bounded review cycles; the compiler should remain the only component that
mutates authoritative state.

### Human gates that cannot be automated away

At minimum:

1. scope, objectives, governance and engagement approval;
2. acceptance of locally governed evidence and survey limitations;
3. agreement of prioritisation criteria, weights/rules and programme horizon;
4. authorisation to consult;
5. verification and disposition of consultation/equality findings;
6. approval of the adoption candidate; and
7. the formal council decision.

The system may record these as signed or linked Governance Directives. It must not impersonate
the decision-maker.

## Delivery proposal

### Phase 0 — Protect the boundary

- Preserve the SATN disclaimer.
- Add an LCWIP conformance report to every future draft.
- Reserve `adopted` for an imported authorised decision record.
- Keep Wayfinding Pass and Prioritisation Pass separate.

### Phase 1 — Define the LCWIP contract

- Create the Guidance Profile, domain model, artifact schemas and lifecycle.
- Define stage gates, explicit unknowns and human authorities.
- Specify adapters rather than hard-coded B&NES data.

### Phase 2 — Build the evidence foundation

- Generalise source governance into a multi-source Evidence Registry.
- Add the minimum national/open adapters and council-local import contracts.
- Publish coverage, freshness and licence reports before running analysis.

### Phase 3 — Complete network planning

- Combine SATN topology with flow-led cycling desire lines and route audits.
- Add a first-class walking/wheeling planner.
- Reconcile strategic and local scales without forcing one network model onto both.

### Phase 4 — Compile interventions and a programme

- Convert deficiencies into typed intervention packages.
- Add outline cost/deliverability evidence.
- Run transparent prioritisation and sensitivity analysis.

### Phase 5 — Add governed agent orchestration and publication

- Add independent critics and bounded revision around each stage.
- Add engagement/equality/policy gates.
- Generate the adoption-candidate report and complete conformance manifest.

### Phase 6 — Pilot and assure

- Run a B&NES pilot with real council-controlled evidence.
- Commission independent transport-planning, accessibility, data, safety and governance
  review.
- Test a second council configuration before claiming portability.
- Re-run the conformance profile when ATE publishes the new LCWIP guidance.

## GitHub delivery backlog

The gap analysis is captured in one umbrella PRD with native sub-issues:

- [#88 — PRD: Build an agent-assisted LCWIP workspace around the SATN compiler](https://github.com/awjreynolds/agentic-satn-compiler/issues/88)
- [#89 — Define the LCWIP domain model, lifecycle and Guidance Profile](https://github.com/awjreynolds/agentic-satn-compiler/issues/89)
- [#90 — Build the governed LCWIP baseline Evidence Registry and adapters](https://github.com/awjreynolds/agentic-satn-compiler/issues/90)
- [#91 — Add cycling demand, desire-line and route-selection planning](https://github.com/awjreynolds/agentic-satn-compiler/issues/91)
- [#92 — Add walking and wheeling network planning and audits](https://github.com/awjreynolds/agentic-satn-compiler/issues/92)
- [#93 — Compile audited deficiencies into infrastructure intervention packages](https://github.com/awjreynolds/agentic-satn-compiler/issues/93)
- [#94 — Add transparent LCWIP prioritisation, phasing and sensitivity analysis](https://github.com/awjreynolds/agentic-satn-compiler/issues/94)
- [#95 — Add LCWIP governance, engagement, equality and human-authority gates](https://github.com/awjreynolds/agentic-satn-compiler/issues/95)
- [#96 — Generalise bounded agent decisions for staged LCWIP review and red-teaming](https://github.com/awjreynolds/agentic-satn-compiler/issues/96)
- [#97 — Publish versioned LCWIP reports, conformance manifests and release history](https://github.com/awjreynolds/agentic-satn-compiler/issues/97)
- [#98 — Track LCWIP delivery, monitoring and scheduled review](https://github.com/awjreynolds/agentic-satn-compiler/issues/98)
- [#99 — Run a B&NES LCWIP adoption-candidate pilot and independent assurance](https://github.com/awjreynolds/agentic-satn-compiler/issues/99)

Issues #89–#98 are specified for agent implementation. The assurance/adoption-candidate
pilot, #99, is intentionally human-owned. The PRD preserves explicit dependencies and
human gates; its sub-issues do not imply that plan adoption can be automated.

The issue tracker should link rather than duplicate the existing SATN work:

- [#11 — Oxfordshire transferable method](https://github.com/awjreynolds/agentic-satn-compiler/issues/11)
- [#20 — original B&NES SATN PRD](https://github.com/awjreynolds/agentic-satn-compiler/issues/20)
- [#31 — Backbone-and-Access Network](https://github.com/awjreynolds/agentic-satn-compiler/issues/31)
- [#59 — bounded agent choices](https://github.com/awjreynolds/agentic-satn-compiler/issues/59)

## Final recommendation

Do not market the present SATN as an LCWIP replacement.

Do market it, accurately, as:

> a reproducible, inspectable strategic cycling-network compiler and a strong technical
> foundation for the cycling-network component of an agent-assisted LCWIP process.

The shortest safe route to an LCWIP is not to weaken SATN's deterministic boundary. It is to
build the missing plan-making layers around it, preserve human authority, and publish a
stage-by-stage conformance record that makes both achievement and uncertainty visible.
