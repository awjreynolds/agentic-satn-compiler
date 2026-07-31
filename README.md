# Agentic SATN Compiler (ASATNC)

ASATNC is an experimental agentic (AI-assisted) network compiler for Strategic
Active Travel Networks. Its deterministic
geospatial core turns governed transport evidence and explicit planning rules into
inspectable network artifacts. At a bounded question requiring deeper
investigation, the compiler can stop and issue a structured request to an AI agent.
The agent examines the supplied evidence and, where an approved integration exists,
may use an external evidence system. It returns a cited finding or selects from
compiler-defined choices; the compiler validates that response before starting a
new versioned run. The agent never edits the network directly.

> Experimental SATN POC — not an adopted plan.

**[Open SATN Deployments](https://awjreynolds.github.io/agentic-satn-compiler/)** — choose
the B&NES or West of England Area Deployment, then inspect Strategic Spines,
access connections, National Cycle Network evidence, Candidate Low-Traffic
Areas, Schools and visible Network Gaps.

Today's published B&NES and West of England maps use **Deterministic Test Mode**.
No live AI model or external evidence system was called. A predefined test
response passes through the same stop, validation and restart boundary so that
the agent workflow can be demonstrated reproducibly. The internal configuration
name for this test substitute is `fake`; it does not mean that the map or
evidence is fabricated.

Every line and gap remains inspectable. The result is a planning hypothesis for
officer discussion, not a scheme design, safety assessment, business case,
consultation result or adopted plan.

## The compiler in one picture

```mermaid
flowchart LR
    area["Area Definition<br/>scope, rules, profiles"]
    snapshot["Immutable evidence snapshot<br/>places, roads, context, elevation"]
    ledger["Optional decision ledger<br/>request + fingerprint + choice IDs only"]

    subgraph compiler["Deterministic compiler — the only network authority"]
        identity["Fingerprint inputs<br/>or reuse an identical validated run"]
        graph["Govern evidence<br/>and build the routable road graph"]
        network["Compile the network<br/>backbone, access, urban fabric, schools, topography"]
        gate{"Bounded decision<br/>point?"}
        validate["Validate the response<br/>and apply a compiler-authored action"]
        gaps["Keep unresolved evidence<br/>as visible Network Gaps"]
        publish["Fingerprint the run<br/>and validate every artifact"]

        identity --> graph --> network --> gate
        gate -- "no" --> gaps --> publish
        validate --> network
    end

    agent["Optional Agent Runtime<br/>selects from a finite menu;<br/>never supplies geometry"]
    outputs["Atomic publication<br/>review map · GeoJSON · GeoPackage<br/>PDF · audit and provenance records"]

    area --> identity
    snapshot --> identity
    ledger --> identity
    gate -.->|one typed request| agent
    agent -.->|request ID + offered choice ID| validate
    publish --> outputs
```

The unusual part is the authority boundary: the system can stop to ask for bounded
investigation or a choice, but the agent cannot invent evidence, submit executable
geometry, change policy or publish a network. A caller response starts a fresh run;
the compiler regenerates the question, checks its dependency fingerprint and applies
its own typed action. Failed or unresolved decisions publish nothing and preserve the
previous valid result.

See **[How the Agentic SATN Compiler works](docs/compiler-architecture.md)** for the
full visual walkthrough of the compilation pipeline, Backbone-Outward Assembly,
key compiler loops, agent decision protocol, input trust boundaries, failure controls
and publication contract.

## Why this matters for the West of England now

The immediate opportunity is not to automate WECA's planning judgement. It is to
give officers one reproducible cross-boundary baseline against which gaps,
assumptions and multimodal access choices can be challenged.

The published
[2020–2036 LCWIP](https://www.westofengland-ca.gov.uk/wp-content/uploads/2021/09/West-of-England-Local-Cycling-and-Walking-Infrastructure-Plan-2020-2036.pdf)
and the
[2026 Transport Vision](https://www.westofengland-ca.gov.uk/wp-content/uploads/2026/02/J13599-Transport-Vision-document-AW-low-res.pdf)
create a changing multimodal planning context. The LCWIP proposed £411 million
of investment, 55 continuous cycle routes and improvements around 30 high
streets. The Transport Vision describes an integrated system of walking,
wheeling, cycling, buses, rail and future mass transit.

Department for Transport
[LCWIP guidance](https://assets.publishing.service.gov.uk/media/5f32aa668fa8f57ac88dc9dc/cycling-walking-infrastructure-technical-guidance-document.pdf)
expects plans to be reviewed approximately every four to five years and when
strategies, development or funding change.

Modes and corridors for mass transit are still to be developed. This creates a
useful moment to expose urban, rural and authority-boundary gaps and compare
first- and last-mile access to possible corridors, interchanges and growth
locations.

The public record supports the case for review, but does not by itself confirm
that a formal full LCWIP refresh has started; that should be confirmed directly
with officers. SATN does not currently design a mass-transit network. It can act
as the active-travel and access-planning companion to that work.

## Beyond SATN: a reusable network-compilation approach

The POC demonstrates more than the ability to draw a Strategic Active Travel
Network. It proves the core approach at POC level: governed evidence and explicit
rules can be compiled into a reproducible network baseline; bounded AI agents can
be asked to investigate difficult questions; human decisions can be recorded as
traceable scenarios; and every output can retain its evidence and decision
history.

That same approach can be used to build a **Mass Transit Network compiler**. The
reusable parts are the evidence governance, deterministic compilation,
agent-investigation boundary, scenario comparison, provenance and publication
model. A mass-transit implementation would replace the active-travel rules with
new governed contracts for matters including:

- candidate corridors, modes, stops, stations and interchanges;
- service patterns, frequency, capacity, journey time and reliability;
- demand, development, equality and first- and last-mile access;
- engineering, land, utility, depot and operational constraints; and
- costs, benefits, phasing and integration with bus, rail and active travel.

This repository does not yet contain that complete mass-transit domain model or
produce a mass-transit network. The POC demonstrates that the compiler
architecture is transferable; building the transit evidence and decision
contracts would be a distinct programme of work with transport specialists.

## What the POC demonstrates

- One compiler can produce council-scale and West of England-scale networks from
  separately versioned area configurations. Agreement on cross-boundary evidence,
  rules and adoption remains a matter for the relevant authorities.
- A-road and established National Cycle Network evidence can seed shared
  Strategic Spines, with community and school access grown outward from them.
- Rural and urban connection failures remain visible Network Gaps rather than
  being hidden by invented straight lines.
- Reviewers can inspect gradients, Candidate Low-Traffic Areas, evidence,
  rationale and source references in the published map.
- The same run produces reproducible map, GeoJSON, GeoPackage, PDF and provenance
  artifacts for technical review.
- The repository contains typed components for wider LCWIP evidence, demand,
  option comparison, intervention and prioritisation work. These components are
  not all presented as a deployed, production-ready end-to-end LCWIP service.
- The separation of governed evidence, deterministic rules, bounded AI
  investigation and attributable scenarios provides a reusable foundation for a
  future Mass Transit Network compiler.

| Status | What is available |
| --- | --- |
| **Published POC today** | Deterministic baseline compilation, inspectable maps and artifacts, visible gaps and provenance. Deterministic Test Mode exercises the agent boundary without calling a live AI model. |
| **Implemented components** | The compiler can stop at bounded decision points and accept validated, traceable responses through provider-neutral agent and recorded-decision contracts. These components are not yet presented as one production service. |
| **Future roadmap** | A complete officer decision workflow, exact route controls, governed agent-assisted imagery surveys, and a separately scoped mass-transit domain implementation. |

The compiler grows a rural Backbone-and-Access Network outward from governed
A-road and established NCN Strategic Spines. Communities and Schools attach to
that shared backbone or remain visible Network Gaps; it does not generate a
nearest-neighbour spider's web. Route choice remains separate from later demand
or delivery prioritisation.

> **AI assurance:** AI is bounded to compiler-defined questions and cited
> evidence. Approved agents may undertake deeper investigation with configured
> external tools, but they cannot create executable route geometry or make policy
> decisions. Only validated compiler actions can create a new versioned network
> run; the baseline is retained and material choices follow the configured human
> acceptance route.

## Baseline first, officer decisions second

A clean baseline uses governed evidence and declared general rules with no
recorded officer or agent decisions. It contains no place-specific or
route-specific discretionary override and remains available as the common
comparison point.

Human judgement is still essential. The domain model and compiler contracts can
record attributable choices from bounded menus, including retaining a gap. The
published POC does not yet expose a complete officer decision workflow.
Officer-informed alternatives should be new, named network runs recording the
responsible officer or role, organisation, date, rationale and evidence; they
must not silently overwrite the clean baseline. Public outputs can identify the
accountable role without exposing unnecessary personal data.

Some important decision types remain future features: excluding an exact road
segment from strategic use, requiring use of a named cycleway, and commissioning
agent-led street-level or aerial imagery observations. They are tracked in
[the officer route-controls issue](https://github.com/awjreynolds/agentic-satn-compiler/issues/236)
and [the future visual-survey issue](https://github.com/awjreynolds/agentic-satn-compiler/issues/238).
The architecture is intended to let a bounded agent investigate an exact
compiler question using an approved service such as licensed Google Street View
or aerial imagery. That provider integration and its licensing, privacy,
provenance and human-acceptance controls are not implemented in the current
published deployments. Desktop imagery would be evidence for human review,
never a substitute for a physical site, safety, engineering or legal survey.

## Proposed WECA discovery

**Decision requested:** nominate an executive sponsor and two or three relevant
LCWIP, cycling, mass-transit or GIS officers for a time-boxed discovery using one
possible transit corridor and one mixed urban/rural catchment. Start with a
60–90 minute working session:

1. Confirm the test corridor, catchment and responsible authority or role.
2. Inspect the clean SATN baseline and its evidence together.
3. Record disagreements as evidence gaps or required officer decision types.
4. Agree the evidence, governance and success criteria for a bounded pilot.

The discovery outputs would be a named evidence snapshot, documented assumptions,
an unresolved-gap log, governance requirements, a comparison approach and a
go/no-go recommendation for a wider pilot. This asks for no network adoption,
procurement or policy commitment. Success means deciding whether traceable
baselines and faster scenario comparison improve officer discussion.

## Technical use

### Install

Python 3.12 or newer and [uv](https://docs.astral.sh/uv/) are required.

```shell
uv sync --all-groups
```

## Compile an Area Definition

Both public commands are driven by the same Area Definition YAML used by the Python
API. An Area Definition can name one authority, several authorities or another
coherent region:

```shell
uv run satn snapshot examples/fixture/council.yaml
uv run satn compile examples/fixture/council.yaml
```

The first command creates an attributable immutable snapshot. The second atomically
replaces `examples/fixture/work/output/` with the current authoritative GeoPackage,
GeoJSON, run and agent records, accessible MapLibre review map, shareable ZIP, and A3
PDF. Governed definitions for the published deployments live under `deployments/`;
their generated data stays under ignored `build/`.

For example:

```shell
uv run satn snapshot deployments/weca/area.yaml
uv run satn compile deployments/weca/area.yaml
uv run python scripts/publish_site.py deployments/weca/area.yaml
```

The West of England definition compiles one continuous regional network across Bath
and North East Somerset, Bristol, North Somerset and South Gloucestershire. Authority
boundaries are retained as visible context; they never clip or fork the network.

The stable library interface is:

```python
from satn import (
    PublishedArtifactReference,
    PublishedNetworkFeatureReference,
    compile,
    published_artifact_reference,
    published_feature_reference,
)

result = compile("examples/fixture/council.yaml")
artifact_reference: PublishedArtifactReference = published_artifact_reference(
    result, "geojson"
)
feature_reference: PublishedNetworkFeatureReference = published_feature_reference(
    result, "a-published-feature-id"
)
```

`PublishedArtifactReference` and `published_artifact_reference` are stable public
publication-contract interfaces for referring to SATN artifacts without copying geometry.
`PublishedNetworkFeatureReference` and `published_feature_reference` identify one feature in a
published GeoJSON artifact, including its source-artifact SHA-256, without copying geometry.

## Snapshot an LCWIP evidence baseline

The LCWIP Evidence Registry is separate from, and compatible with, the existing
`satn snapshot` source bundle. A council- and Guidance-Profile-specific registry
configuration declares its required evidence families and governed local inputs:

```shell
uv run lcwip evidence snapshot path/to/evidence-registry.json
uv run lcwip evidence validate path/to/evidence-snapshots/snapshot-id
```

The snapshot is immutable and content-fingerprinted. Each item records its source,
publisher, licence, retrieval and observation dates, spatial coverage, version,
methodology, known bias, quality, permitted uses and one of the governed evidence
roles. Adapter contracts cover population, equality context, travel demand, collision
and traffic evidence, public transport and attractors, development and policy, rights
of way and infrastructure, and controlled local imports.

Every snapshot contains `coverage-report.json` and `coverage-report.md`. The reports
make missing, stale, low-quality, spatially incomplete, licence-restricted and
non-reproducible evidence explicit and produce typed evidence requests. Call
`load_evidence_gate` before later LCWIP analysis; it revalidates hashes and reconstructs
both reports from the manifest.

```python
from pathlib import Path

from lcwip import (
    EvidenceRegistryConfig,
    load_evidence_gate,
    snapshot_evidence_registry,
)

config = EvidenceRegistryConfig.from_json(Path("path/to/evidence-registry.json"))
snapshot = snapshot_evidence_registry(config)
coverage = load_evidence_gate(snapshot)
```

Public evidence is copied into the bundle, controlled evidence must use a separately
redacted input or be excluded, and personal evidence is always excluded. Derived
evidence requires complete input lineage and a transformation version. Agents may
classify or request evidence through these contracts but cannot mutate snapshot items
or mark unavailable evidence present.

## Plan cycling demand and route selection

The LCWIP Demand Planning Pass runs outside the SATN Wayfinding Pass. It consumes a
validated Evidence Snapshot, governed OD points/flows and only immutable public SATN
artifact/feature references. SATN remains one network hypothesis: demand-led agreement,
Network Gaps and local/external divergence are reported without changing the compiled
SATN network.

```python
from lcwip import build_demand_analysis

bundle = build_demand_analysis(
    demand_config,
    points=od_points,
    scenarios=demand_scenarios,
    flows=governed_flows,
    satn=satn_public_hypothesis,
    routing_boundary=deterministic_router,
)
```

`deterministic_router` implements the small `DemandRoutingBoundary` protocol. Each
request names one retained Cycling Desire Line and a configured finite alternative
limit; the adapter may use the existing deterministic SATN routing engine but can
return only typed route candidates. The Demand Planning Pass rejects detached routes,
unoffered SATN features, excess alternatives, stale decision menus and evidence IDs
that do not resolve in the Evidence Registry.

Distance bands, trip thresholds, local/strategic scales, equality protection,
endpoint tolerance and sensitivity cases are configuration. The immutable result
retains the unsimplified flow and desire-line long list, complete aggregation lineage,
every filter outcome, current-condition and potential-design-outcome assessments,
rejected alternatives, explicit unknowns and bounded human/agent decisions. Network
density reports count covered and uncovered OD points; they are not delivery priority.

Each bundle publishes:

- `demand-network.geojson` for OD points, long-list desire lines, alternatives and gaps;
- `route-selection.json` with route audits and SATN reconciliation;
- `sensitivity.json` with threshold sensitivity;
- `review-map.html` with an accessible spatial and tabular review surface; and
- `conformance-artifacts.json` with `baseline-evidence` and
  `cycling-network-plan` links for the active Guidance Profile.

Validate a received bundle, including all cross-artifact hashes and derivations, with:

```shell
uv run lcwip demand validate path/to/demand-output/analysis-id
```

## Plan walking and wheeling networks

The Walking and Wheeling Planning Pass is independent of cycling and SATN
geometry. It consumes a validated Evidence Snapshot, walking trip attractors,
configured catchments, reviewable Core Walking Zone proposals and walking route
specifications. A small deterministic routing boundary supplies walking-specific
Key Route and Funnel Route geometry.

```python
from lcwip import build_walking_plan

bundle = build_walking_plan(
    walking_config,
    attractors=walking_attractors,
    catchments=walking_catchments,
    zones=core_walking_zone_proposals,
    route_specifications=walking_route_specifications,
    audit_observations=walking_audit_observations,
    lived_experience_findings=privacy_safe_findings,
    routing_boundary=deterministic_walking_router,
)
```

Walking audits cover footway continuity and width, surface, crossings, gradient,
severance, lighting and personal safety, seating/rest opportunities and
wayfinding. Condition provenance (`observed`, `inferred`, `modelled`, `unknown`)
is separate from evidence mode (`desktop`, `site-survey`, `lived-experience`,
`none`). Mandatory missing site evidence creates a typed Evidence Request and
structurally prevents a route or area from being marked fully audited.

Wheelchair, mobility-aid, visual, hearing, cognitive/neurodivergent, resting and
personal-safety needs are explicit planning data. They are not satisfied by a
browser-accessibility check. Controlled access-panel material enters public
outputs only through governed redaction with personal data removed, and material
lived-experience findings remain subject to the accountable officer and
accessibility-representative gate.

Each immutable bundle publishes:

- `walking-network.geojson` and a self-contained `review-map.html`;
- `walking-audits.json` and `walking-evidence-requests.json`;
- `walking-deficiencies.json` for the intervention workflow;
- `engagement-input.json` containing privacy-safe typed findings; and
- `conformance-artifacts.json` linking the walking-network, audit, engagement
  and intervention-input artifacts.

Validate the bundle and all derived files with:

```shell
uv run lcwip walking validate path/to/walking-output/analysis-id
```

## Compile infrastructure intervention packages

The Infrastructure Intervention Pass converts accepted cycling and
walking/wheeling deficiencies into a shared strategic programme contract without
copying or conflating the audit models that produced them. Every normalized
deficiency reference retains its source workflow, source artifact and
fingerprint, source record, governed evidence, affected modes/users and
accountable human acceptance.

```python
from lcwip import build_intervention_packages

bundle = build_intervention_packages(
    intervention_config,
    deficiencies=accepted_deficiency_references,
    outcomes=desired_design_outcomes,
    concepts=catalogue_bound_concepts,
    costs=governed_outline_cost_ranges,
    constraints=constraint_assessments,
    packages=strategic_packages,
)
```

The versioned Intervention Catalogue permits route-section, junction, crossing,
area, supporting-infrastructure, wayfinding and maintenance concepts. A concept
must use an exact catalogue entry/version and supported geometry, modes and
users. It records approximate location, assumptions, alternatives, dependencies,
mutually exclusive options, residual deficiencies and an explicit
`detailed_design_in_scope = false` boundary.

Outline costs are ranges, not invented point estimates. They expose currency,
price base, rounding increment, basis, confidence, included/excluded scope,
quantity assumptions, unknowns, governed expert evidence and human verification.
Missing cost or unknown land/highway-rights, environment/heritage, utilities,
traffic, dependency, maintenance or survey/design evidence remains a typed
Evidence Request. Feasible or designed status requires separately governed
human stage evidence and resolved constraints.

The immutable bundle publishes concept GeoJSON, package and traceability tables,
cost/constraint evidence, Evidence Requests, conformance links and a review
surface that separates network aspiration, intervention concept and delivery
status. Validate it with:

```shell
uv run lcwip interventions validate path/to/intervention-output/analysis-id
```

## Compare and phase programme scenarios

The Prioritisation Pass consumes a validated intervention bundle and
council-approved, fingerprinted criteria. Each measure belongs to one separately
inspectable view—effectiveness/benefit, policy/equality or
deliverability/cost—and records its source observation, evidence, transform,
weight, missing-data rule and contribution. SATN traffic lights, validity states
and assembly order are rejected as priority measures.

```python
from lcwip import build_prioritisation

bundle = build_prioritisation(
    criteria=approved_criteria,
    intervention_bundle=intervention_bundle,
    output_dir=output_dir,
    analysis_id="banes-prioritisation",
    observations=governed_measure_observations,
    scenarios=approved_scenarios,
    sensitivity_cases=approved_sensitivity_cases,
)
```

Missing evidence produces an Evidence Request and leaves the intervention
unranked and unphased; it never becomes zero or an average. Scheduling retains
cost confidence, risks and unresolved requests, respects hard dependencies and
mutually exclusive options, and uses explicit short/medium/long horizon cost
envelopes. Sensitivity outputs show rank and phase instability under plausible
weight and input changes, including conflicting policy scenarios.

Analytical scenarios are not programmes. A Recommended Programme requires a
recorded human recommendation, and an Authorised Programme requires a later
human authorisation of that same scenario. Agents cannot choose policy weights,
programme scenarios or funding commitments. Validate outputs with:

```shell
uv run lcwip prioritisation validate path/to/prioritisation-output/analysis-id
```

## Govern engagement, equality and adoption

The Governance Record binds plan sponsorship, the SRO, project board, council
directives, objectives, targets and timetable to named human authorities. It also
records the engagement strategy, stakeholder register, accessible activities,
immutable source-representation hashes, agent summaries with source citations and
coverage, and separate human dispositions. Public output includes public or redacted
representations only; excluded personal material is never copied into that output.

Lifecycle state is derived from a contiguous transition history. Analysis,
consultation, adoption-candidate and adopted states require their exact named human
gates, and cumulative validation prevents a permitted state transition from bypassing
consultation. Equality findings with unknown or unresolved adverse impacts block
consultation and adoption. Policy alignments cite a governed policy clause, the
affected plan subject and a named officer's judgement.

Software does not adopt an LCWIP. An `adopted` record is possible only after an
external authority decision is independently verified by a different person and
bound to the exact substantive release fingerprint. Use
`governance_release_fingerprint` to bind that decision, then build and validate the
immutable record:

```shell
uv run lcwip governance validate path/to/governance-output/release-id
```

Representation limits and groups not reached remain visible, and every record states
that silence is not support. Post-consultation amendments retain their trigger,
responsible human, rationale and release-fingerprint chain.

## Run bounded staged agent reviews

LCWIP stage reviews use one provider-neutral, fingerprinted decision envelope rather
than a “write the plan” prompt. Eight closed role contracts cover evidence critique,
cycling, walking/accessibility, interventions, prioritisation, engagement synthesis,
network/design red-teaming and report/citation review. Each role declares its
Evidence Packet scope, finite action vocabulary, permitted output, prohibited claims,
citation duty, retry budget, revision budget and hard wall-clock deadline.

Evidence text is explicitly untrusted data. A runtime returns only the request ID,
dependency fingerprint, one offered action ID and governed evidence citations. It
cannot submit executable fields or actions for raw-evidence mutation, policy weights,
mandatory waivers, manufactured representations or adoption. Missing evidence creates
a typed Evidence Request; an accountable policy choice, bounded-loop failure or
no-consensus creates the smallest possible Human Intervention Request.

```python
from lcwip import (
    apply_validated_stage_decision,
    build_stage_decision_envelope,
    review_stage,
)

envelope = build_stage_decision_envelope(
    stage=stage,
    role=role,
    compilation_scope=scope,
    governed_target_ids=target_ids,
    plan_state_fingerprint=plan_state.fingerprint,
    evidence_packet=evidence_packet,
    actions=compiler_authored_actions,
    deterministic_action_id="1",
)
review = review_stage(envelope, runtime=optional_runtime, agent_enabled=use_agents)
mutation = apply_validated_stage_decision(
    plan_state,
    review,
    critique_gate=independent_critique_gate,
)
```

Stale fingerprints, malformed responses, timeouts, provider failures and fabricated
replay records fail closed while retaining prompt, provider/model/runtime, input,
response-hash, usage and validation metadata. Selected stages require an independently
fingerprinted critic record bound to the primary request. Blocking and
revision-required findings remain blocking until evidenced resolution, an allowed
named-human waiver, or explicit unresolved escalation.

No-Agent Mode selects the declared deterministic fallback without constructing a
runtime and produces the same typed artifacts. Only the deterministic compiler can
append an applied decision to authoritative state, and that state has no mutation
surface for source content, policy weights, lifecycle state or adoption.

Validate serialised envelopes and data-only replay ledgers with:

```shell
uv run lcwip agents validate-envelope path/to/envelope.json
uv run lcwip agents validate-ledger path/to/ledger.json
```

The default Deterministic Test Agent requires no credentials and makes no model
or external-system call. Its internal provider name is `fake`. It supplies a
predefined response through the same typed compilation gate used by configured
AI model providers, allowing that boundary to be tested reproducibly.

## Publish an immutable LCWIP release bundle

`lcwip.publication` compiles a governed `PublicationConfig` into one atomic,
immutable release directory. It publishes a cited PDF report and accessible HTML
summary, matching GeoJSON and GeoPackage network plans, programme schedule,
conformance manifest, privacy-safe source-quality and decision/agent audit records,
semantic and spatial release diffs, immutable release history, and a shareable ZIP.

Every material narrative claim resolves to governed citations. Unsupported adoption,
feasibility or funding language is rejected unless it carries matching typed authority
evidence bound to the exact release fingerprint. Mandatory conformance gaps,
unresolved representations and equality findings remain prominent blockers. The
default adoption-candidate banner is explicit: it is not an adopted plan.

The Python boundary is `build_lcwip_publication(config)`. Validate a generated bundle
and every cross-artifact hash, watermark, citation, GIS feature, accessible web
contract and PDF page with:

```shell
uv run lcwip publication validate path/to/releases/release-id
```

Publication is fail-closed: generation occurs in a temporary sibling directory and
is renamed into place only after complete validation. An existing release ID is
content-immutable. A later adoption annotation must identify the exact substantive
release fingerprint and distinct verification evidence; it does not rewrite the
underlying release.

## Track delivery, outcomes and governed review

`lcwip.monitoring` creates an immutable monitoring release without rewriting the
publication manifest, evidence fingerprints or conformance result of the historical
adopted release. Programme updates are separate tagged records for design, funding,
construction, completion and outcome state. Every update carries a public source,
observer or authority, observation and recording dates, confidence, verification
state and exact scope fingerprint.

The monitoring release exposes overdue and blocked milestones, unverified or
contradicted reports, planned-versus-delivered scope deviations, late or missing
indicator observations, and target status. Indicator definitions bind a baseline,
target, method, unit and reporting cadence; observations bind their period, coverage
and uncertainty. Activity and observed outcomes remain separate and the public
dashboard makes no causal claim.

Scheduled review, material development, network delivery, guidance change, evidence
expiry and underperformance are typed Review Triggers. They create governed Review
Tasks; they do not mutate plan state. A superseding release proposal must point to the
exact predecessor release fingerprint, a new evidence snapshot, the re-evaluated
Guidance Profile and the triggers that caused review. Guidance and evidence migration
reports identify every affected analysis and programme entry. A progress-only cycle
may have no trigger or proposal and publishes an empty review-task set.

Build with `build_monitoring_release(config)` and validate the privacy-safe HTML tables
and machine-readable status, review, migration and comparison artifacts with:

```shell
uv run lcwip monitoring validate path/to/monitoring-releases/cycle-id
```

## Snapshot B&NES from OpenStreetMap

The reference configuration contains only council data and thresholds; acquisition
and place derivation remain portable compiler behaviour:

```shell
uv run satn snapshot config/banes.yaml
```

OSM acquisition uses the governed `source.overpass_url` and
`source.osm_timeout_seconds` settings. The B&NES configuration decomposes its broad
evidence tags and uses a ten-minute per-request timeout because its buffered source
queries can exceed the default three-minute budget.

This retrieves the full governed boundary, cycling graph, named place features,
public-transport stations and everyday amenities from OpenStreetMap, plus the current
National Cycle Network from the Walk Wheel Cycle Trust public feature service. The
immutable snapshot records retrieval time, content hashes, OSM/ODbL attribution and
NCN/Open Government Licence v3.0 attribution. Towns, villages and named urban neighbourhoods are
admitted as Community candidates; hamlets are not mandatory Network Places. Large
Community polygons can expose connected network portals, and genuine outward road
crossings are named for the relevant external town or city along the onward corridor.
The Council Configuration also governs which OSM place types define urban extents and
their evidence buffer. Snapshot creation splits A-road and established NCN linework at
that extent, recording each continuous part as `rural` or `urban`; invalid scope values
are rejected rather than silently omitted. This lets the normal live snapshot path
produce rural Strategic Spines without hard-coding a B&NES-only geography.

The network request is intentionally live and can take time. Its explicit smoke test
is:

```shell
uv run pytest --live-osm -m live_osm tests/test_osm_sources.py
```

For replayable Local Evidence, acquire the network separately as a bounded raw OSM
XML Source Export:

```shell
uv run python scripts/acquire_osm_network.py \
  --bbox 51.28 -2.39 51.40 -2.19 \
  --cache-dir .satn/evidence/osm \
  --endpoint https://overpass-api.de/api/interpreter \
  --timeout-seconds 180
```

This is the only operation in the Local Evidence path that contacts Overpass. It
issues a closed `way["highway"]` plus referenced-node query for the exact WGS84
bbox, then validates the response through the closed OSM adapter before publishing
anything. Exact received bytes are retained as
`objects/sha256/<digest>.osm`; retrieval, query, area, HTTP metadata, OSM
`generator`/`osm_base`, ODbL attribution and the Source Export declaration are
retained in a content-addressed receipt. `load_acquisition_receipt()` verifies that
receipt and raw object offline and returns the Source Export consumed by Local
Evidence refresh. Run the command separately for disconnected areas; no intervening
geography is acquired. Ordinary `satn snapshot` and `satn compile` keep their
existing behaviour and never invoke this operation.

## How routes are compiled

Schema 2.0 uses Backbone-Outward Assembly as the only authoritative rural network
model. All governed Strategic Spines seed one concurrent frontier. Each iteration
serves the nearest reachable rural Community, extending an existing branch where that
is cheaper than creating another direct spine attachment. The compiler compares only
continuous, bidirectional OSM alignments.

Where one strongly connected cycling component contains at least 90% of graph nodes,
Community attachment prefers that dominant routable component instead of snapping to
a nearby isolated digitising fragment.

The Backbone-and-Access tracer promotes only governed rural A-road and established NCN
evidence. Its first Spine Access Connection publishes only routed OSM geometry between
canonical graph attachment points. Bounded Community and spine snap distances, node IDs
and point coordinates remain visible and auditable metadata; they are not drawn or
validated as paths without traversable network evidence.

- a direct A-road corridor is the preferred Strategic Spine, representing a wide
  alongside shared path rather than cycling in the carriageway. Its authoritative
  geometry is an indicative corridor centreline; the review map offsets it
  cartographically to show “alongside” without inventing survey-level geometry;
- a parallel route is used when alongside provision is explicitly found physically
  impracticable, with that reason retained;
- other rural connections compare direct and low-traffic OSM paths;
- where no qualifying A-road spine wins, NCN overlap can inform the selected
  continuous alignment;
- no continuous two-way path becomes a visible Red Network Gap rather than a drawn
  straight line; and
- a connection over 15 km is challenged Amber, not silently removed.

In urban areas, governed official A roads, B roads and Classified Unnumbered Roads
form protected-route spines. Unclassified streets do not become through-traffic
spines, and OSM functional tags do not silently replace missing official evidence.
When no governed classification source is configured, the publication records an
`explicit-unknown` classification status. Urban NCN Evidence remains a separate
permeability layer rather than automatically becoming a Circulation Boundary. Connected
fabrics of minor roads become Candidate Low-Traffic Area polygons, without claiming
that an LTN already exists or asserting an artificial centreline. Official Urban
Main-Road Spines, mapped built-up edges adjoining open land, substantial rivers,
canals and railways can close those fabrics; administrative wards, property lines and
individual field parcels cannot. Each candidate records its intervention need and
stable named portals to the qualifying boundary network. Observed internal through
traffic strengthens the intervention need but never promotes an unclassified street
into a through-traffic spine.

For reproducibility, a river, canal or surface railway segment must provide at least
250 metres of continuous physical boundary evidence before it can close a candidate.

Urban School Access Obligations are assessed against this same topology. A mapped or
inferred usable entrance is served only when it shares continuous low-traffic street
or path fabric with a named portal on an Urban Main-Road Spine. The published point
record cites the candidate area, portal and supporting source identifiers; it never
draws a preferred route through residential streets. Unresolved entrances, missing
main-road portals and discontinuous fabric remain visible findings.

Every in-scope School also receives a separate preliminary School Street Candidate
Assessment. Green/Promising, Amber/Needs Investigation, Red/Unlikely and Grey/Not
Evaluated markers summarise entrance evidence, adjoining official road class, bus and
essential access, alternative through-route evidence and displacement risk. Unknown
inputs remain explicit; an inferred entrance cannot produce Green or Red. These are
qualitative investigation prompts, never scheme feasibility or calibrated probability.

Every generated Community Connection, Strategic Spine section, Spine Access
Connection, Branch Meeting Connection, Cross-Spine Connector and Urban Main-Road
Spine also receives a directional Topography Profile. Governed Elevation Evidence
sampled throughout the edge records distance, cumulative ascent and descent in both
directions, steepest sustained gradient and stable Gradient Sections. The adjustable
display bands are Gentle (up to 3%), Noticeable (above 3% to 5%), Steep (above 5% to
8%), Very Steep (above 8% to 12.5%) and Severe (above 12.5%). These sections are
always visible. A bounded Topography Alternative comparison is triggered by 100 m of
Steep, 50 m of Very Steep, 30 m of Severe, or repeated shorter climbs with material
cumulative ascent. It may select a longer materially easier plausible Alignment Option,
but it keeps and visibly flags the original when none is easier. Gradient never removes
a Strategic Spine. Missing or unusable Elevation Evidence is published as an explicit
Grey evidence-unavailable Topography Profile and unresolved comparison; it never implies
flat terrain.

The display bands and evidence-spacing rules are governed trial configuration. The
maximum spacing prevents sparse endpoint-only evidence from being mistaken for a
profile, while the minimum sustained spacing suppresses isolated sub-window noise.
Governed short steeper sections still contribute to cumulative ascent/descent and
remain visible with an explicit non-sustained rationale; only the separately named
steepest-sustained-gradient statistic applies the sustained window.

```yaml
compilation:
  topography:
    gentle_max_pct: 3
    noticeable_max_pct: 5
    steep_max_pct: 8
    very_steep_max_pct: 12.5
    maximum_sample_spacing_m: 250
    minimum_sustained_spacing_m: 10
    steep_trigger_length_m: 100
    very_steep_trigger_length_m: 50
    severe_trigger_length_m: 30
    repeated_climb_count: 2
    cumulative_ascent_trigger_m: 40
    maximum_alternative_detour_ratio: 1.5
    material_ascent_reduction_m: 20
    material_ascent_reduction_ratio: 0.25
```

Each compiled Community Connection records whether comparison triggered, the original
and selected roles, the reason for selection or retention, and an Elevation Evidence
summary for every candidate Alignment Option. These are deterministic bounded
refinements before the existing agent Compilation Gate; routine choices do not require
per-edge human approval.

Offline fixtures can supply `source/elevation-evidence.geojson` containing Point
samples with `evidence_id`, `source_id`, `effective_date`, `licence` and
`elevation_m`. Snapshot creation validates and fingerprints that governed file, and
normal compilation loads it without a live lookup. National terrain acquisition for
real council snapshots uses the same contract. A configured local GeoJSON or remote
GeoJSON point service is clipped to the governed boundary plus compilation buffer,
canonicalised, attributed and fingerprinted in the immutable snapshot:

```yaml
source:
  national_elevation:
    provider: local-geojson # or remote-geojson with url
    path: data/local/national-terrain-samples.geojson
    source_id: national-dtm-2026
    effective_date: 2026-01-15
    licence: Open Government Licence v3.0
    attribution: National terrain model
    elevation_field: elevation_m
    identifier_field: evidence_id
```

Remote sources receive the governed bounding box and must return a GeoJSON
FeatureCollection of Point samples. Compilation performs no live lookup: it reads the
snapshotted `elevation-evidence.geojson`. Changing that evidence changes the snapshot
and run fingerprints. Sparse OSM `ele` and `incline` tags are published only as
`corroborating-only` evidence and never substitute for missing national coverage. Run
the optional live-source smoke test explicitly with `--live-terrain` and
`SATN_TEST_TERRAIN_GEOJSON_URL`.

For English councils, create a bounded local evidence file from the Environment
Agency LIDAR Composite DTM WCS after compiling or otherwise supplying eligible route
linework:

```shell
uv run python scripts/acquire_ea_elevation.py \
  build/compiled/AREA/review-map/network.geojson \
  data/local/ea-lidar-dtm-1m-council-samples.geojson \
  --cache-dir data/local/ea-dtm-cache \
  --spacing-m 10
```

For the WECA deployment, `deployments/weca/area-bootstrap.yaml` is the **bootstrap**
Area Definition and `deployments/weca/area.yaml` is the elevation-enabled final
deployment. The governed two-pass workflow is therefore non-circular: snapshot and
compile the bootstrap definition; derive four named authority polygons from the retained
official boundary source into `data/local/weca-authority-boundaries.geojson` (with
stable authority IDs and source queries); fetch the exact official WFS response;
then acquire the 10 m evidence and add the existing `national_elevation` local-GeoJSON
block only for the final retained-core snapshot. The authority artifact must be
derived from an official boundary download with its stable source IDs and exact
source query retained; labels alone are not sufficient. Do not alter the provenance
lock by hand: the release process regenerates it from that final snapshot.
The final Area Definition uses a different immutable snapshot ID and declares the
historical bootstrap `snapshot_id` plus its exact `snapshot.json` SHA-256 under
`source.retained_core_source`. `satn snapshot ... --retain-core` verifies that source,
copies only its manifest-validated regular core siblings into a missing final target,
then adds configured elevation and governed spatial evidence atomically. Do not
manually copy a snapshot directory. A repeated
command validates and reuses the completed final target only when its recorded lineage
still matches; it never rewrites the historical benchmark snapshot.
Only the final WECA definition sets `acquisition_contract: ea-lidar-weca-v1`.
That opt-in is intentional: existing B&NES and other council EA GeoJSON sources
remain ordinary national-elevation evidence rather than being forced through the
WECA ledger/index protocol.

```shell
uv run satn snapshot deployments/weca/area-bootstrap.yaml
uv run satn compile deployments/weca/area-bootstrap.yaml --full
curl --fail --location --output data/local/ea-lidar-composite-dtm-1m-weca-survey-index.geojson \
  'https://environment.data.gov.uk/spatialdata/survey-index-files/wfs?service=WFS&version=2.0.0&request=GetFeature&typeNames=dataset-9f0fa3fc-a860-4729-adc9-47fe53f658d0%3ALIDAR_Composite_1m_DTM_2022_extents&srsName=EPSG%3A27700&bbox=306127%2C134191%2C397868%2C215091%2CEPSG%3A27700'
uv run python scripts/derive_weca_authority_boundaries.py \
  data/local/official-boundaries.geojson data/local/weca-authority-boundaries.geojson \
  --name-field NAME --id-field GSS_CODE \
  --source-query 'official boundary dataset URL and exact query/version'
```

Require that pinned survey index and the four constituent-authority boundaries before
downloading:

```shell
uv run python scripts/acquire_ea_elevation.py \
  build/compiled/weca-bootstrap/review-map/network.geojson \
  data/local/ea-lidar-dtm-1m-weca-samples.geojson \
  --cache-dir data/local/ea-dtm-cache \
  --spacing-m 10 \
  --authority-boundaries data/local/weca-authority-boundaries.geojson \
  --survey-index data/local/ea-lidar-composite-dtm-1m-weca-survey-index.geojson \
  --weca-preflight \
  --routing-buffer-m 15000 \
  --governed-input-fingerprint "$(uv run python -c 'from satn.models import AreaDefinition; from satn.pipeline import compilation_governed_input_fingerprint; area = AreaDefinition.from_yaml("deployments/weca/area-bootstrap.yaml"); print(compilation_governed_input_fingerprint(area))')"
uv run satn snapshot deployments/weca/area.yaml --retain-core
uv run satn compile deployments/weca/area.yaml --full
```

The ignored survey index is itself a small governed contract: the current pinned
contract is the exact governed eligible-route extent plus 15 km (not an over-broad
council or response envelope), and every polygon must pin
the official dataset UUID, coverage ID, WCS endpoint, OGL licence, EA attribution and
effective survey date. The preflight reports `available`, `partial` or `unavailable`
for B&NES, Bristol, North Somerset and South Gloucestershire. It is intentionally
per `(route_id, sample_index)`, so coincident endpoints on different routes remain
separate observations; missing samples and cross-boundary discontinuities stay
explicit rather than being hidden by coordinate de-duplication. Both the resulting
evidence manifest and the final snapshot preserve source and pre-elevation-network
hashes. A snapshot copies and hashes the EA index and authority-boundary artifacts,
then rewrites the sidecar to these immutable sibling paths; it does not depend on
the mutable acquisition directory at publication time.

The command downloads only intersecting 5 km tiles, scales the official 1 m DTM to
the requested route-sampling interval, preserves request URLs and tile hashes in a
manifest, and excludes aggregate Cross-Spine Connector geometry. Configure the
result through the existing `local-geojson` contract, then create the separate final
retained-core snapshot with `--retain-core`; do not replace the immutable historical
source snapshot. The 3D terrain layer is contextual and is never sampled for analysis.

Configure a council-governed classification dataset as follows. The source must be
line geometry with an `official_classification`, `road_classification` or
`classification` field; common A, B, C/Classified Unnumbered and Unclassified values
are normalised in the immutable snapshot.

```yaml
source:
  official_road_classification:
    path: data/local/official-road-classification.geojson
    source_id: council-highways-list
    effective_date: 2026-04-01
    licence: Open Government Licence v3.0
```

Observed internal through-traffic can also be supplied as governed line evidence.
The compiler snapshots its source, effective date, licence and content fingerprint,
then marks intersecting Candidate Low-Traffic Areas as needing intervention without
promoting their internal streets into the through-traffic network.

```yaml
source:
  observed_through_traffic:
    path: data/local/observed-through-traffic.geojson
    source_id: council-traffic-study
    effective_date: 2026-03-01
    licence: Open Government Licence v3.0
```

## Agent compilation gate

Council Configuration selects exactly which Criterion Statuses require agent review.
The default is Amber and Red; Green is deterministic and Grey can be selected
independently. A selected status returns a
typed, fingerprinted Agent Decision Request containing the exact criterion, governed
evidence, deterministic findings and finite compiler-authored choices.
An unselected Green, Amber or Grey decision is applied deterministically, while an
unselected Red still becomes an explicit Network Gap. No Criteria Section aggregate
is used to choose review.

```yaml
compilation:
  agent:
    provider: fake
    response_mode: caller
    review_statuses: [amber, red]
```

The public library returns `decision-required` with the currently actionable menu and
no partial artifacts. The CLI prints the equivalent JSON and exits immediately; it
does not read stdin, poll, sleep, maintain a heartbeat or retain a live continuation.
Each choice is a simple identifier such as `1`, `2` or `terminate`, and already states
the action, consequence and constraints the compiler will enforce. An empty
`review_statuses` list guarantees that no Agent Runtime is constructed or called.
Agents cannot invent actions, mutate compiled state or override a Red mandatory
criterion.

To apply a choice, start a fresh compilation with a data-only decision ledger. Each
response contains only the regenerated request identifier, its dependency fingerprint
and one offered choice identifier; executable actions and free-form instructions are
rejected. The compiler locates the same decision point, revalidates the fingerprint,
membership and mandatory invariants, then applies its own typed action. Stale, unknown
or cross-request responses return the current `decision-required` menu without changing
compiled state. `terminate` returns a non-publishing `terminated` result and preserves
the previous valid publication.

```json
{
  "decision_contract": "agent-decision-menu/v1",
  "responses": [
    {
      "request_id": "agent-decision-…",
      "dependency_fingerprint": "…",
      "choice_id": "1"
    }
  ]
}
```

```shell
uv run satn compile config/banes.yaml --decision-ledger decisions.json
```

Accepted choices participate in publication fingerprints and are repeated in the run
manifest and Agent Decision Records. Choices affecting published spatial features are
also linked through spatial properties and accessible review-map details; ATM comparison
choices remain in typed divergence records because they never mutate authoritative
geometry. Identical governed inputs and the same ledger reproduce the same identifiers
and authoritative artifacts.

Set `response_mode: direct-runtime` to let the configured Agent Runtime answer the
same menu in-process. The runtime receives the complete fingerprinted request and may
return only `request_id` and one offered `choice_id`; the compiler supplies and validates
the fingerprint and applies its own predefined action. Each call is limited to one
request and attempt, `max_tokens`, and the hard `deadline_seconds` wall-clock limit.
Timeout, provider failure, malformed output, request mismatch or an unknown choice
returns the same non-publishing `decision-required` result immediately. The accepted
record identifies `direct-runtime` as the responder and includes provider, model, usage
and choice-validation provenance. A caller decision ledger still takes precedence and
does not construct the runtime.

```yaml
compilation:
  agent:
    provider: pydantic-ai
    model: your-model-name
    response_mode: direct-runtime
    review_statuses: [amber, red]
    deadline_seconds: 30
    max_requests: 1
    max_attempts: 1
    max_tokens: 4000
```

Routine unresolved refinements remain typed findings and visible gaps. Historical
free-form Agent Runtime records remain readable, but compilation no longer creates
proposal, critique, red-team, synthesis or divergence action-selection calls. A
`human-intervention-requests.json` record records any retained historical exhausted
review, unresolved findings, missing evidence, available choices and the smallest human input
needed. Ordinary no-path or missing-spine gaps do not create intervention requests.

The Deterministic Test Agent (`provider: fake`) is the default. Any Pydantic AI
model identifier can be supplied in Council Configuration as
`compilation.agent.model`, with provider credentials read from its normal
environment variables. Codex is not required. A live adapter check is explicit
and opt-in:

```shell
SATN_TEST_AGENT_MODEL=openai:gpt-5-mini \
  uv run pytest --live-agent -m live_agent tests/test_agent_gate.py
```

## Network assembly

The compiler produces one authoritative rural Backbone-Outward Assembly. Every governed Strategic Spine is available
as a seed before growth begins. The compiler repeatedly selects the globally nearest
reachable unserved Community by bidirectional cycling-network cost, then adds that
Community's canonical graph attachment to the served frontier. A hinterland Community
can therefore inherit its root spine and branch through an already served Community
instead of originating another direct spine route.

Every Spine Access Connection records its root spine, owning branch, immediate parent,
attachment depth, source edge IDs and deterministic rationale. Degree-one served
Communities are valid leaves. Unreachable Communities become point-only Network Gaps,
and meaningful Cross-Boundary Gateways attach to the same served frontier without
becoming Community Access Obligations. Stable sorting and identifiers make topology
independent of source feature order.

When independently rooted fronts reach locally adjacent Communities, their first
validated cycling-network meeting becomes a Branch Meeting Connection. Meetings are
selected in global cost order and join spine components as a tree, so parallel or cyclic
links cannot turn the result into a mesh. Each published Cross-Spine Connector traces
that meeting through both parent lineages to the relevant Strategic Spines, preserving
the full connection, branch, evidence and agent-gate provenance.

Rural primary, secondary, all-through and special Schools are separate School Access
Obligations rather than Network Places. A mapped usable entrance is preferred; a point
inferred from a mapped school-boundary/path intersection remains Amber and provisional,
while an unresolved entrance is never silently snapped to a road. Each resolvable School
selects one governed attachment to fixed spine, connector or established branch geometry
after Community growth, so it can reuse a branch without becoming a frontier for another
School or generating school-to-school or school-to-Community journey pairs. Colleges and
universities remain contextual evidence unless their OSM source IDs are explicitly listed in
`source.strategic_destination_source_ids`.

The previous pairwise network is absent from compilation, GeoPackage, GeoJSON, PDF and
review-map layers. If an earlier publication exists, `backbone-comparison.json`
summarises topology, gaps, linework/noise and explainability differences under the
explicit role `superseded-reference-not-ground-truth`; agreement with it is not a
correctness criterion. `publication.comparison_reference` may identify a governed
tracked predecessor. The B&NES configuration uses an immutable schema-1 summary under
`references/`, so a clean full compile remains reproducible after the schema-2 site is
promoted.

## Deterministic recompilation

Schema 2.0 does not read or write the legacy pairwise Connection cache. A default run
may reuse only a complete, validated schema-2 publication whose source snapshot,
governed configuration, criteria, schema and compiler implementation fingerprints all
match. Otherwise it reassembles the authoritative backbone from the immutable snapshot.
Stable identifiers and ordering make unchanged runs topologically and fingerprint stable.
Changing any governed input invalidates whole-publication reuse.

```shell
# compile the authoritative backbone
uv run satn compile config/banes.yaml

# force a complete deterministic rebuild
uv run satn compile config/banes.yaml --full
```

CLI commands emit timestamped standard-library logs to stderr. `INFO` is the default
and reports acquisition groups, snapshot counts, periodic backbone and School progress,
publication validation and elapsed time. Long-running snapshot and compile stages also
emit a structured `event=satn_heartbeat` liveness record every 30 seconds with the
current stage, area context and elapsed time. Use `--log-level DEBUG` for selected
frontier decisions and artifact diagnostics, or `WARNING`, `ERROR` or `CRITICAL` for
quieter automation:

```shell
uv run satn compile config/banes.yaml --log-level DEBUG
```

`run.json` retains deterministic compilation diagnostics: graph/search dimensions,
candidate evaluations, frontier growth and typed optimisation findings.  It also
records the explicit, fail-closed compilation dependency manifest (schema, set
version, installed compiler-runtime distributions and per-component SHA-256
values).  Package components use stable `satn/...` labels and text component
hashes ignore only LF/CRLF checkout differences. Pages, deployment, PDF and
review-map code are not compiler semantics; their current publication validation
still runs before a previous publication is reused. Wall-clock
timings and throughput remain in the logs because they are operational observations,
not reproducible governed output. Changing `compilation.criteria_version` invalidates
whole-publication reuse.

### Cross-Spine benchmark

Cross-Spine traversal reports a versioned deterministic diagnostic object under
`compilation_diagnostics.cross_spine`: candidate and authoritative connector counts,
Route Refinement Findings, and logical graph/search work including peak queue and
graph dimensions. It does not retain elapsed time or machine-specific observations.

To validate the WECA-scale Cross-Spine release gate without publishing a deployment,
run the local deterministic-fake-runtime paired benchmark. It runs the retained eager
reference and the lazy schedule in separate deterministic-seed subprocesses, compares
the complete same-runtime governed contract (including ordered WKB, provenance and
typed findings), and records independent wall/CPU/RSS evidence. Normal tests do not
run this regional compile: routine regression and CI use B&NES or smaller synthetic
fixtures only. The script requires an explicit Area Definition, so the WECA gate can
only be invoked deliberately.

```shell
.venv/bin/python scripts/benchmark_cross_spine.py deployments/weca/area-125-benchmark.yaml \
  --output build/benchmarks/weca-cross-spine-paired.json
```

The canonical WECA command uses the tracked, byte-for-byte #125 Area Definition
fixture (`deployments/weca/area-125-benchmark.yaml`), rather than the evolving
deployable WECA definition. It requires the retained local historical snapshot
`data/snapshots/weca-osm-current/snapshot.json` with its recorded digest; if that
snapshot is absent or differs, it fails closed without running either worker.
It also fails closed unless both workers retain the #125 Area Definition and
snapshot-manifest digests, the 2,211 logical root pairs, exact
same-current-compiler identity, and #125's separately recorded source-inclusive
baseline fingerprint. The compiler source digest is deliberately not pinned to its
historical value: this optimisation changes compiler source, and substituting the old
digest would falsify provenance. The gate also requires no more than 100 materialised
root-pair routes, `network_compile` wall/CPU at or below 1,200 seconds, lower lazy CPU
than eager, lazy RSS below 4 GiB and no more than 10% above eager. The evidence JSON
is ignored process output, not a tracked deployment artifact. The lazy worker records
root-group distance-planning searches and settled nodes separately: the 100-route
limit applies only to governed route materialisation, not hidden precomputation. If
two root groups have no shared reciprocal routing component, the lazy worker records
that component-eligibility proof separately and avoids only the route call that would
deterministically return no candidate. If the two governed signatures differ, the
paired evidence records the first differing
governed field and its two SHA-256 values; the mismatch remains a hard failure.

## ATM quality comparison

ATM is an optional B&NES quality reference, not a portable network rule or an
authoritative answer. Put the locally governed file at
`data/local/banes-atm-full.geojson`, set `atm.enabled: true`, and choose one mode:

- `blind` compiles routes before the ATM file is loaded, then compares them;
- `seeded` records that the reference was available before comparison, but schema 2.0
  does not permit it to replace or steer the authoritative Backbone-Outward result.

The typed divergence output distinguishes matches, deviations, additions and
omissions. Each uses its own governing Criterion Status and the same configured agent
review policy; there is no aggregate agreement score and a match does not prove
correctness.

For `publication.audience: public`, ATM geometry is omitted unless
`atm.redistribution_permitted: true`. A `local` review may include the overlay. The
default public B&NES configuration keeps comparison disabled and redistribution
false because public retrieval is not, by itself, a licence grant.

The public review map still provides a governed local-file control. Select an ATM
GeoJSON in the browser to load it only into that browser session, then toggle “ATM
reference (before/after)” without uploading or republishing its geometry.

## Review and share the result

Every successful compile replaces the configured output directory only after all
artifacts validate against each other:

- `network.gpkg` is the authoritative multi-layer spatial output;
- `network.geojson`, `run.json`, `agent-records.json`,
  `human-intervention-requests.json`, `divergence-records.json` and
  `backbone-comparison.json` expose the same governed run;
- `review-map/` is a backend-free static site with a vendored, pinned MapLibre build;
- `review-map.zip` contains that complete directory unchanged as a local build
  artifact, but is not published by the Pages catalogue; and
- `network-map.pdf` is A3 landscape by default, with configurable A2/A3/A4 size,
  title, date, legend, scale and disclaimer.

Open `review-map/index.html` directly or serve the directory from any static host.
The map presents Strategic Spines, Spine Access Connections and Cross-Spine
Connectors as the core rural network, with NCN evidence as a distinct overlay.
Schools, derived high-street/retail
centres and healthcare facilities default off to avoid noise; each has its own
control. Road-classification evidence gaps and LTA portal evidence also default off,
keeping the circulation-plan view legible while remaining independently auditable.
Places, urban structure, gaps/warnings and ATM comparison are independently toggleable.
The map itself carries a compact, collapsible legend for every symbol shown by the
default layers, so the key remains available when the controls are off-screen on mobile.
Gradient Sections use a separately selectable sequential blue terrain
palette with a text-labelled legend distinct from Criterion Status colours; Grey
dashed profiles expose unavailable Elevation Evidence. Connections, Network,
Topography and ATM criteria remain separate. Hover or keyboard
focus updates the semantic details panel; click pins and unpins it. The panel exposes
Community names, stable IDs, length, role, indicative intervention, criterion states,
rationale, findings and source IDs, so browser agents do not need to infer state from
the map canvas.

Publisher tests include cross-artifact identifiers, ZIP equivalence, PDF extraction
and preservation of the previous output after a simulated print failure. Install the
pinned Chromium once and run the interaction test explicitly:

```shell
uv run playwright install chromium
uv run pytest --browser -m browser tests/test_review_map_browser.py
```

The Pages root is a lightweight
[SATN Deployment Catalogue](https://awjreynolds.github.io/agentic-satn-compiler/), not an alias
for one authority. Each Area Deployment has a stable interactive map, printable A2/A3
PDF and portable Review Map ZIP. The initial map loads only the strategic network and
named constituent-authority boundaries. Optional contextual and gradient evidence is
split into content-addressed spatial shards, loaded for the active view when selected,
and cached by the browser on a best-effort basis.

Build each validated Area Deployment independently, then assemble the ignored Pages
release bundle:

```shell
uv run python scripts/publish_site.py deployments/banes/area.yaml
uv run python scripts/publish_site.py deployments/weca/area.yaml
uv run python scripts/package_pages.py
```

`build/satn-pages.zip` is used as temporary release transport by the Pages workflow
and deleted from the public release after a successful deployment. Generated
snapshots, compiled outputs, deployment directories, PDFs, ZIPs and Pages trees are
reproducible process artifacts and are not committed to Git. The
packager fails before publication if the configured Pages size budget would be
exceeded. Runtime-governance and urban-road evidence remain published and
provenance-bound for inspection, but their review status does not block publication
of this public proof of concept.

Canonical Pages publication always runs the production release gate; manual dispatch
cannot bypass it. To inspect a non-production package locally, use the standalone
validator's explicit review exception:

```shell
.venv/bin/python -I scripts/validate_pages_release.py build/satn-pages.zip \
  build/validated-pages-review --catalogue deployments/catalogue.yaml \
  --allow-non-production
```

This local exception does not publish, approve the runtime or turn the deployment
into a production SATN. The interface is designed and recommended for desktop use;
smaller devices receive the same data contract without being the primary supported
experience.

## Check

```shell
uv run ruff check .
uv run pytest
```

## Status

The POC compiler now treats the schema-2 Backbone-and-Access Network as authoritative.
A full B&NES publication must be generated from its governed immutable snapshot; no
legacy pairwise output or cache is accepted as a replacement when that evidence is
unavailable. This is an experimental generated network, not an adopted plan, and its
alignments still require scheme-level feasibility and design work.

Released under the MIT licence. OpenStreetMap-derived outputs must retain
OpenStreetMap attribution and comply with the ODbL. NCN-derived outputs must retain
Walk Wheel Cycle Trust attribution and comply with the Open Government Licence v3.0.
