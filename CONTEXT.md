# Agentic SATN Compiler (ASATNC)

This context defines the language for a council-portable agentic compiler that develops one continuous, evidence-led Strategic Active Travel Network from connections between places.

## Language

**Agentic Network Compiler**:
A deterministic geospatial compiler that can stop at bounded decision points and issue structured investigation requests through an optional provider-neutral AI Agent Runtime. An approved agent may analyse governed evidence and use explicitly configured external evidence systems, then return a cited finding or select from compiler-authored actions. The deterministic compiler validates the response and alone creates a new versioned run; the agent cannot invent evidence, submit executable geometry, set policy or adopt a network. The published POC uses Deterministic Test Mode (`provider: fake`), which calls neither a live AI model nor an external system.
_Avoid_: autonomous planner, AI route author, chatbot map, production AI claim

**Network Compiler Pattern**:
The reusable separation of governed domain evidence, explicit rules, deterministic network compilation, bounded agent investigation, attributable human scenarios, provenance and publication. The SATN POC demonstrates this pattern for strategic active travel. A Mass Transit Network compiler may reuse the pattern only after defining specialist contracts for corridors, modes, stops, services, capacity, demand, operations, constraints, costs and multimodal integration.
_Avoid_: transport-mode-agnostic model, completed mass-transit compiler, automatic network design

**Community**:
A named, inhabited settlement or recognisable urban neighbourhood admitted as a Network Place. It is not defined by an administrative ward, a universal population threshold or an individual destination.
_Avoid_: ward, destination, settlement point

**Community Reference Point**:
The single canonical point used to represent and attach a compact Community in one compilation. It uses the Community Centre where practical, otherwise a source representative point; no inhabited-area footprint is required.
_Avoid_: Community Footprint, arbitrary settlement point

**Community Portal**:
One of multiple canonical points where an external Community Connection meets the internal network of a physically extensive Community.
_Avoid_: boundary crossing, arbitrary entrance

**Urban Community**:
A named neighbourhood within a larger settlement that has its own cluster of everyday services and is admitted as a Community, preventing the larger settlement from collapsing into one network endpoint.
_Avoid_: ward, suburb label, city-centre spoke

**Community Centre**:
A named local high street or dense cluster of everyday services that anchors an Urban Community and must be reached by its internal network.
_Avoid_: city centre, individual shop, arbitrary centroid

**Community Amenity Profile**:
Qualitative present, absent or unknown facts about everyday services in a Community, used to explain local usefulness without creating Network Places, Community Connections or an unexplained score.
_Avoid_: destination list, demand score, required access

**School**:
A primary, secondary, all-through or special education site admitted as a School Access Obligation. A college or university remains contextual evidence unless separately admitted as a Strategic Destination.
_Avoid_: education site, college, university

**School Access Point**:
The usable School entrance used to assess network access and School Street plausibility, recorded as mapped, inferred or unresolved. An inferred point may be proposed from boundaries, gates, paths and adjoining streets but remains unverified and cannot alone support a Green or Red assessment.
_Avoid_: School representative point, automatic nearest-road snap, assumed main entrance

**Urban School Access Assessment**:
An inspectable School Access Obligation record showing whether a usable urban School Access Point shares continuous low-traffic street or path fabric with a named Low-Traffic Area Portal on an Urban Main-Road Spine. It cites the Candidate Low-Traffic Area, portal and supporting evidence while representing the internal journey as area permeability rather than a selected residential centreline.
_Avoid_: urban school route, school-to-school journey, residential centreline

**School Street Candidate Assessment**:
A preliminary agentic assessment of whether a timed motor-traffic restriction outside a School is Green/Promising, Amber/Needs Investigation, Red/Unlikely or Grey/Not Evaluated, using evidence about usable entrances, adjoining road classification, bus and essential access, alternative through-traffic routes and displacement. It expresses qualitative plausibility for human investigation, not scheme feasibility or a calibrated probability.
_Avoid_: School Street decision, probability score, guaranteed intervention

**Layer Legend**:
The visible and accessible explanation of every colour and symbol used by a contextual map layer, displayed whenever that layer is active and available from its layer control. It uses text labels as well as colour so people and browser agents can interpret the layer.
_Avoid_: colour-only key, hidden help, always-visible unrelated legend

**Low-Traffic Area**:
An urban network area with defined portals and sufficiently permeable low-traffic internal streets or paths that an Alignment Option need not assert one exact centreline through it.
_Avoid_: single route, Community boundary, guaranteed LTN

**Candidate Low-Traffic Area**:
A proposed Low-Traffic Area inferred from a connected unclassified-street fabric enclosed by Urban Main-Road Spines and, where necessary, non-road settlement edges. Existing through traffic creates an intervention need rather than turning an internal street into a spine, and the area does not claim that low-traffic conditions already exist.
_Avoid_: existing LTN, administrative neighbourhood, quiet-road assumption

**Low-Traffic Area Portal**:
A stable named point where continuous internal low-traffic street or path fabric actually meets a qualifying Circulation Boundary of a Candidate Low-Traffic Area. It supports area permeability and School access without asserting a preferred internal centreline and is distinct from a Community Portal.
_Avoid_: Community Portal, approximate nearest point, selected residential route

**Backbone-and-Access Network**:
A delivery-led network structure in which continuous Strategic Spines provide shared routes, selected Cross-Spine Connectors provide transverse routes, and Communities and Schools reach them through bounded access. It avoids a dense web of repeated point-to-point routes.
_Avoid_: pairwise network, nearest-neighbour network, spider's web

**Strategic Network Visualization**:
A deliberately bounded, informative and inspectable layered picture showing the Backbone-and-Access Network, Gradient Sections, School Access Obligations and Candidate Low-Traffic Areas together. It explains a prioritised strategy for building outward from shared spines rather than claiming that every displayed corridor is already complete or designed. Street-level imagery inspection and detailed intervention derivation are future refinement work, not prerequisites for generating this picture.
_Avoid_: final scheme map, cycle-route inventory, undifferentiated linework

**Strategic Network Route Layer**:
The topmost red route layer in the Strategic Network Visualization, combining governed A roads, established National Cycle Network routes, Declassified NCN Routes and Greenway Cycleways. It remains visually distinct from contextual, analytical and warning layers.
_Avoid_: separate competing spine overlays, contextual route colour, hidden backbone

**Backbone-Outward Assembly**:
The iterative formation of a Backbone-and-Access Network from all Strategic Spines concurrently, extending through the nearest reachable unserved Access Obligations and joining differently rooted branches where they first meet. It ends with every Access Obligation served or exposed as a Network Gap.
_Avoid_: one-spine-at-a-time build, global pairwise routing, order-dependent catchment

**Access Obligation**:
A Community or School that must be served by the backbone without requiring a connection to another Community or School. A degree-one Access Obligation is valid once its applicable backbone-access rule is satisfied.
_Avoid_: peer network node, redundancy requirement, direct journey pair

**Network Place**:
A named endpoint admitted to the network as a Community, standalone Strategic Destination or interchange, or Cross-Boundary Gateway.
_Avoid_: arbitrary endpoint, map point, School Access Obligation

**Community Connection**:
The single selected access link between distinct adjacent Communities when it extends access toward a Strategic Spine or forms part of a Cross-Spine Connector. A Spine Access Connection is evidence for a Community Connection only when its child and parent are both identified Network Places for those Communities.
_Avoid_: arbitrary neighbour link, route alternative, duplicate link

**Local Adjacency**:
An evidence-backed relationship between nearby Network Places measured over the plausible cycling network. It emerges through recursive compilation and network validation rather than a fixed neighbour count; unusually long candidates are challenged rather than automatically excluded.
_Avoid_: fixed-radius link, all-to-hub connection, k-nearest rule

**Cross-Boundary Gateway**:
A Network Place at the governed study-area boundary with a named onward place or network connection.
_Avoid_: clipped endpoint, map-edge stub

**Gateway Destination**:
The nearest relevant town or city outside the governed study area reached along a Cross-Boundary Gateway's onward corridor. Intervening villages may inform routing but do not name the gateway.
_Avoid_: nearest settlement, boundary label, arbitrary external point

**Network Terminus**:
A degree-one endpoint of the authority-wide network, normally a Cross-Boundary Gateway. A Community is a Network Terminus only when no credible onward connection exists and the reason is recorded.
_Avoid_: dead end, dangling route

**Alignment Option**:
One evidence-backed, end-to-end way of realising a Community Connection. Only one Alignment Option may be selected into a published network.
_Avoid_: parallel connection, final design

**Alignment Candidate Set**:
The finite, evidence-backed Alignment Options generated for one strategic Community Connection before substitute/complementary classification and selection.
_Avoid_: unconstrained route search, alternative network

**Preferred Strategic Alignment**:
The one selected Alignment Option for a substitute Alignment Candidate Set under a declared Network Selection Profile, with rejected alternatives and change conditions retained for inspection. It is not final design, a safety finding, feasibility evidence or a funding decision.
_Avoid_: objectively correct route, scheme approval, preferred scheme

**Network Selection Profile**:
A frozen, versioned, data-only local policy declaration that orders candidate classes and defines how otherwise eligible Alignment Options are compared, including population, education, existing-alignment, ambiguity and publication policies.
_Avoid_: hidden score, agent policy, mutable setting

**Population Reach Profile**:
The governed whole-Output-Area measure of residents whose population-weighted centroids lie within a declared straight-line corridor around an Alignment Option, with its source date, radii and sensitivity retained. It is not predicted demand, accessible population or a walking-time claim.
_Avoid_: five-minute catchment, demand model, connected homes

**Education Access Profile**:
The frozen, evidence-bounded declaration of which education-access measures may inform Alignment Option comparison, including the independent-travel phases and the boundary between school-register and supplementary evidence. It does not create a school safety finding, travel-demand model or access guarantee.
_Avoid_: school-safety policy, school-mode-share model, accessibility guarantee

**Independent-Travel Opportunity**:
A separate, evidence-bounded comparison of how an Alignment Option may support secondary-age or all-through secondary education access under declared measures. It does not claim that a route is safe, suitable or independently accessible.
_Avoid_: school safety verdict, accessibility guarantee, demand score

**Existing-Alignment Advantage**:
A bounded selection advantage based on separately recorded recognised-corridor, reusable-asset or delivery evidence. Current route status alone does not establish condition, legal access, low cost or feasibility; declassified status alone receives no advantage.
_Avoid_: existing-route preference, assumed cheapness, feasibility finding

**Strategic Education Destination**:
A college, university or other non-school education site admitted by a versioned local record as a strategic destination. It remains contextual evidence until that admission exists.
_Avoid_: every education site, School Access Obligation, implied destination

**Scenario Compilation**:
One immutable compilation defined by an Area Definition, evidence snapshot, Criteria Set, Network Selection Profile and accepted decisions. It may be compared with other scenarios but does not itself create authority for a Reference SATN.
_Avoid_: mutable scenario, adopted network, live policy view

**Baseline Scenario Compilation**:
A Scenario Compilation using governed evidence and declared general rules with an empty accepted-decision ledger and no feature-specific discretionary directives. It is retained as the clean comparison point for every officer-informed alternative.
_Avoid_: hidden override, adopted network, only possible scenario

**Local Evidence Store**:
A single-user, embedded and rebuildable query store derived from already-downloaded authoritative source exports. It supports fast spatial and attribute subset selection for Scenario Compilation without becoming the authoritative source, requiring a managed service, or repeatedly parsing whole national datasets.
_Avoid_: source of record, managed database, mandatory daemon, multi-user platform, opaque cache

**Evidence Refresh**:
The explicit local phase that imports, normalises and spatially indexes already-downloaded authoritative source exports in the Local Evidence Store. It runs only when governed source content or its ingestion contract changes, not when a Scenario Compilation configuration changes.
_Avoid_: scenario build, automatic re-download, per-compilation import

**Evidence Coverage**:
The recorded set of spatial partitions and source versions currently available in a Local Evidence Store. Coverage may contain disconnected council or study areas; adding Oxfordshire, for example, does not require importing the geography between Oxfordshire and an existing B&NES partition.
_Avoid_: continuous expansion frontier, national preload, Area Definition

**Scenario Iteration**:
The repeated compilation and comparison of different governed configurations against one unchanged Local Evidence Store. It reuses indexed source evidence and dependency-valid derived facts so changing scenario configuration does not trigger Evidence Refresh or whole-dataset parsing.
_Avoid_: evidence ingestion, mutable scenario, full source rebuild

**Edge Enrichment**:
A reusable derived fact bound to a stable network-edge identity, geometry fingerprint, governed source fingerprints, algorithm version and the parameters that affect the result. Scenario Compilations consume dependency-valid Edge Enrichments without owning them; only a changed dependency invalidates or creates a different enrichment.
_Avoid_: scenario-owned statistic, unversioned cache value, whole-network recomputation

**Reference SATN**:
The Scenario Compilation selected through the applicable governed human process as the clear default strategic network for review and publication. Other Scenario Compilations remain comparisons.
_Avoid_: automatically adopted network, only possible network, final scheme

**Officer-Informed Scenario Compilation**:
A new immutable Scenario Compilation containing attributable accepted human decisions, including responsible officer or role, organisation, date, rationale and evidence, and compared with rather than silently substituted for its Baseline Scenario Compilation. Public metadata need not expose personal details beyond the accountable role.
_Avoid_: mutable override layer, hidden exception, rewritten baseline

**Network Gap**:
An unresolved absence of a continuous, bidirectionally traversable connection with plausible intervention coverage. A Network Gap prevents a network being Complete.
_Avoid_: visual gap, omitted link

**Route Refinement Finding**:
A recorded defect in an Alignment Option, such as a discontinuity, invalid join, excessive detour, or uncovered intervention need, that must be repaired or become a Network Gap.
_Avoid_: automatic snap, hidden error

**Crossing Warning**:
A non-blocking indication that selected route geometries cross without a shared Junction Node. It invites agentic inspection for a useful missing junction but does not imply that the crossing must connect.
_Avoid_: topology failure, automatic junction, prohibited crossing

**Quiet Lane**:
A rural lane whose low motor-traffic conditions and treatment make it a plausible active-travel alignment; the term does not imply that through motor traffic is prohibited.
_Avoid_: traffic-free lane, access-only lane

**Access-Only Quiet Lane**:
A rural lane where through motor traffic is physically or legally filtered while authorised access, including landowner, property and emergency access, remains.
_Avoid_: Quiet Lane, traffic-free path

**Strategic Spine**:
A continuous rural backbone corridor defined by an A road, an established National Cycle Network route, a Declassified NCN Route or a Greenway Cycleway. An A-road spine is selected for strategic continuity and is presumed to require substantial engineering for high-quality provision alongside the road rather than carriageway cycling; neither a route-quality gap nor an Elevation Challenge removes the corridor from the backbone.
_Avoid_: rural B-road spine, cycling on the A-road carriageway, guaranteed NCN quality

**Declassified NCN Route**:
An official Walk Wheel Cycle Trust reclassified route that formerly formed part of the National Cycle Network and remains governed strategic cycle-route evidence. It is sourced from the separate official Reclassified Routes dataset and is not inferred from an RCN code.
_Avoid_: current NCN route, Regional Cycle Network route, discarded historic linework

**Greenway Cycleway**:
A traffic-free Greenway section identified by the official cycle-route source and retained as a Strategic Spine. Where it is also part of the current NCN, its Greenway role is preserved without duplicating the corridor.
_Avoid_: any path with “green” in its name, assumed current NCN, duplicate route

**A-Road Spine Intervention Assumption**:
The default evidence position that every A-road section admitted as a rural or urban spine requires major engineering to provide safe, generous, physically separated walking, wheeling and cycling space. Existing provision changes that position only when evidence demonstrates a continuous high-quality facility; the strategic network does not prescribe one detailed facility design.
_Avoid_: cycle-ready A road, painted-lane assumption, final shared-path design

**Cross-Spine Connector**:
A rural transverse corridor that emerges when Spine Access Branches grown outward from different Strategic Spines meet through adjacent Communities, allowing travel to distinct onward destinations on either spine. It is an outcome of backbone-outward assembly rather than a separately imposed corridor or independent dual attachment for every Community.
_Avoid_: connector quota, preselected lateral route, pairwise mesh

**Branch Meeting Connection**:
The single Community Connection added where Spine Access Branches rooted in different Strategic Spines first reach adjacent Communities. It completes an emergent Cross-Spine Connector without admitting parallel meeting links between the same growth fronts.
_Avoid_: general cross-link, redundant connector, branch overlap

**Spine Access Point**:
The canonical point where a rural Access Obligation reaches a Strategic Spine, selected by the shortest reachable plausible cycling alignment rather than straight-line proximity.
_Avoid_: nearest geometric point, destination, arbitrary junction

**Spine Access Connection**:
The bounded connection from a rural Access Obligation to its nearest reachable Strategic Spine, Cross-Spine Connector or already-served Community with onward backbone access.
_Avoid_: arbitrary point-to-point route, complete journey, spine segment

**Spine Access Candidate Preparation**:
The bounded generation and pre-admission audit of finite routing alternatives for already-compiled chained Community-to-Community Spine Access Connections, together with an exhaustive disposition roster. A direct Strategic Spine attachment remains Spine Access and is retained as explicitly out of scope rather than analysed as a two-place alternative; an unresolved row remains an explicit preparation gap.
_Avoid_: Preferred Strategic Alignment evidence preparation, Community Connection selection, route decision

**Spine Access Branch**:
A recursively assembled chain or tree of Spine Access Connections grown outward from a Strategic Spine or Cross-Spine Connector through already-served Communities.
_Avoid_: independent nearest-neighbour links, general-purpose mesh, disconnected feeder

**School Access Obligation**:
A requirement for a rural School to have a valid Spine Access Connection, or for an urban School's usable entrance to connect through continuous Low-Traffic Area street or path fabric to a portal on an Urban Main-Road Spine. It does not create a route to a Community or another School, and an urban residential-street centreline is not asserted.
_Avoid_: School Network Place, school-to-school route, school-to-Community route

**Urban Main-Road Spine**:
An urban A Road, B Road or Classified Unnumbered Road assigned to carry through motor traffic, bound Low-Traffic Areas and provide protected cycling infrastructure along the corridor.
_Avoid_: shared-use default, unprotected carriageway route, residential-street cycle route

**Urban NCN Evidence**:
The published geometry of an established National Cycle Network route retained where it passes through an urban area. It may evidence internal walking, wheeling and cycling permeability or School access, but does not become an Urban Main-Road Spine, Circulation Boundary or justification for through motor traffic.
_Avoid_: urban through-traffic spine, invented internal route, LTN boundary

**Classified Unnumbered Road**:
A smaller road officially classified to connect unclassified roads with A and B roads, often called a C road locally. Any local C-road number has no standard national meaning.
_Avoid_: nationally numbered C road, unclassified street, OSM tertiary road

**Urban Circulation Plan**:
A city- or town-wide arrangement that confines through motor traffic to Urban Main-Road Spines and treats the areas between them as Candidate Low-Traffic Areas. Its boundaries may include non-road settlement edges where the classified-road network does not enclose the area.
_Avoid_: cycle-route map, current traffic description, residential through-route plan

**Circulation Boundary**:
A stable edge that encloses a Candidate Low-Traffic Area: an Urban Main-Road Spine, the built-up edge adjoining open land, or a substantial barrier such as a river, canal or railway. Administrative, property and field-parcel lines do not qualify by themselves.
_Avoid_: ward boundary, property boundary, arbitrary field edge

**Intervention Archetype**:
A plausible category of treatment that could make part of an indicative alignment accessible and traversable without asserting detailed feasibility or design.
_Avoid_: final design, unfunded promise

**Detour Factor**:
The measured relationship between an Alignment Option's travel distance and the direct distance between its Network Places, used to trigger challenge rather than impose one universal cutoff.
_Avoid_: directness pass/fail

**Elevation Challenge**:
A visible condition on any network edge where sustained local gradient, cumulative climbing or repeated elevation change is likely to make ordinary cycling materially harder. Noticeable Gradient Sections are informational, and minor steep sections do not by themselves require an alternative route. A material challenge triggers comparison and may favour a longer but less demanding option, but it never disqualifies a Strategic Spine or forces rejection when no better alignment exists.
_Avoid_: hill ban, hidden routing penalty, assumed accessibility

**Gradient Section**:
A continuous part of a network edge for which local gradient severity and sustained length are displayed. Initial adjustable severity bands are Gentle at up to 3%, Noticeable above 3% and up to 5%, Steep above 5% and up to 8%, Very Steep above 8% and up to 12.5%, and Severe above 12.5%. Noticeable and short steeper sections remain visible even when they do not affect route selection. The bands use a sequential terrain palette distinct from Criterion Status colours and never imply that the edge is invalid.
_Avoid_: endpoint-average gradient, isolated noisy sample, red-means-rejected

**Topography Alternative Trigger**:
An adjustable trial condition requiring comparison with an easier Alignment Option when an edge contains a Steep Gradient Section for at least 100 metres, a Very Steep section for at least 50 metres, a Severe section for at least 30 metres, or repeated shorter climbs whose cumulative ascent makes the route materially harder. Triggering comparison does not reject the original edge when no materially better alignment exists.
_Avoid_: gradient prohibition, invisible routing weight, permanent design standard

**Topography Profile**:
The distance and per-direction cumulative-ascent, cumulative-descent, Gradient Sections and steepest-sustained-gradient evidence displayed for an Alignment Option to support human and agent judgement. It is derived from elevation throughout the alignment rather than endpoint difference, and the measures are not collapsed into a composite effort score.
_Avoid_: net elevation change, cycling effort score, hidden weighting

**Micro-Gradient Interval**:
A distance-aligned 20 metre detail or 50 metre overview measurement derived from governed elevation samples no more than 12.5 metres apart. It records direction, severity, supporting evidence and uncertainty; unavailable evidence remains explicit rather than being interpreted as level ground.
_Avoid_: map-tile gradient, assumed flat interval, endpoint-only slope

**Gradient Inspection Path**:
An ordered, continuous selection of eligible Published Features assembled from one active endpoint for exploratory analysis. Aggregate Cross-Spine Connector geometry is excluded because its constituent edges already carry the analytical evidence.
_Avoid_: arbitrary multi-selection, disconnected edge set, aggregate double-counting

**Linear Evidence Panel**:
A shared-distance view of the Gradient Inspection Path that aligns Micro-Gradient Intervals with road classification and future engineering evidence tracks. Reversing the path reverses directional gradient without changing the governed source evidence.
_Avoid_: independent charts, edge-only summary, composite route score

**Contextual Terrain Mode**:
An optional visually exaggerated 3D terrain view used for orientation. It is never an analytical elevation source, and failure of its replaceable raster-dem provider restores the default 2D map without affecting the network or Linear Evidence Panel.
_Avoid_: analytical terrain tile, required 3D renderer, MapToolkit dependency

**Elevation Evidence**:
Governed terrain-height evidence sampled along the routable network to produce Topography Profiles. A national terrain model is authoritative for continuous coverage; sparse OSM elevation and incline tags are corroborating evidence rather than the primary source.
_Avoid_: OSM-only elevation, live elevation lookup, assumed flat terrain

**Bridge Connection**:
A Community Connection whose removal would split the network into disconnected parts.
_Avoid_: weak link

**Articulation Place**:
A Network Place whose removal would split the network into disconnected parts.
_Avoid_: critical town

**Reviewable Network**:
A published network state that may contain visible Network Gaps and open Route Refinement Findings for inspection.
_Avoid_: complete network, failed output

**Inspectable Review Map**:
A static browser map whose selected-feature details, statuses and controls are mirrored in accessible HTML with stable identifiers, allowing people and browser agents to inspect the network without relying on the rendered map canvas alone.
_Avoid_: canvas-only map, GIS-only output, screenshot report

**Review Map Bundle**:
The read-only static directory, shareable ZIP and GitHub Pages deployment generated from the same current network layers and Inspectable Review Map implementation.
_Avoid_: editable map, hosted application, separate publication build

**Complete Network**:
A connected, bidirectionally traversable Backbone-and-Access Network with continuous Strategic Spines and Cross-Spine Connectors, every Access Obligation served, complete intervention-archetype coverage, and no blocking Route Refinement Findings. Degree-one Access Obligations are valid and do not require redundant edges.
_Avoid_: final design, adopted network

**Evidence Packet**:
An immutable, versioned collection of governed evidence and rules supplied to an agent role for one compilation scope.
_Avoid_: prompt context, live web research

**Source Export**:
The immutable governed received artifact selected as authoritative for one source family, dataset and layer. Its raw bytes and declared release provenance identify it; a download location or local cache does not.
_Avoid_: download URL, cache file, latest source

**Evidence Partition**:
The stable source-layer and spatial-cell subset of a Source Export, represented by its content record and Source Export attestation. It is spatial coverage rather than a council or Area Definition.
_Avoid_: council cache, database page, requested area

**Scenario Configuration**:
A frozen, data-only combination of an Area Definition, Criteria Set, Network Selection Profile and other declared choices for one Scenario Compilation. Accepted decisions are separate governed input, not mutable configuration.
_Avoid_: live settings, decision ledger, user session

**LCWIP Evidence Registry**:
The governed catalogue of LCWIP baseline Evidence Items, their stable identities, provenance, permitted uses, access policy and reproducibility state. It describes evidence honestly; it does not acquire missing data or permit an agent to change source facts.
_Avoid_: shared data folder, agent memory, evidence claim

**LCWIP Evidence Item**:
One immutable registry record classified by Evidence Family and Evidence Role, with publisher, licence, retrieval and observation dates, spatial coverage, version, methodology, known bias, quality and permitted uses. An unavailable item records why it cannot be reproduced instead of appearing present.
_Avoid_: unreferenced file, prompt attachment, inferred fact

**Evidence Family Requirement**:
A Guidance-Profile- and council-specific declaration of the spatial coverage, freshness, quality and permitted use required from one Evidence Family before an analytical pass. It is configured evidence governance, not a universal threshold hidden in code.
_Avoid_: global completeness rule, adapter default, acceptance of political risk

**LCWIP Evidence Snapshot**:
An immutable, content-hashed public bundle containing the Evidence Registry manifest, permitted or redacted evidence artifacts, and machine- and human-readable coverage reports. Sensitive or personal source material is excluded; a changed governed input creates a different snapshot rather than mutating the bundle.
_Avoid_: live source cache, mutable baseline, private-data export

**Evidence Coverage Report**:
The deterministic account of missing, stale, low-quality, spatially incomplete, licence-restricted or non-reproducible Evidence Families for one LCWIP Evidence Snapshot. Later analytical passes load this report through the Baseline Evidence Gate and never interpret an omitted or unavailable source as satisfactory evidence.
_Avoid_: confidence score, completeness assertion, evidence substitution

**Evidence Lineage**:
The complete stable identifiers of the governed inputs to a derived Evidence Item together with its transformation version. Lineage is acyclic and does not turn a transformation into a new raw source.
_Avoid_: free-text citation, partial dependency list, hidden calculation

**Controlled Evidence**:
Evidence retained outside public artifacts because it is sensitive or personal. A governed public snapshot may contain only an explicitly redacted derivative, or an exclusion record with its non-reproducibility reason.
_Avoid_: silently copied consultation response, anonymous-by-assumption data

**Baseline Evidence Gate**:
The mandatory validation boundary that reconstructs and verifies a snapshot's machine and human Evidence Coverage Reports before an LCWIP analytical pass consumes them. It exposes limitations but does not decide whether incomplete evidence is politically acceptable.
_Avoid_: automatic approval, agent override, optional report view

**Agent Decision Record**:
A schema-valid audit record for a compilation decision, stating its governing Criterion Status, effective Agent Review Policy, whether review was required, and either the complete fingerprinted menu and accepted caller choice, the typed direct-runtime result, or the deterministic outcome. A caller-mediated record includes the mapped compiler action, responder mode, validation result and affected feature identifiers. It never directly changes compiled state.
_Avoid_: free-form answer, silent edit, unrecorded deterministic skip

**Agent Review Policy**:
The exact set of Green, Amber, Red and Grey Criterion Statuses in Council Configuration that require an Agent Decision Request. It applies to the status governing an individual decision, never a Criteria Section aggregate; an empty set means no Agent Runtime is constructed or called.
_Avoid_: always-on agent, worst-section rollup, open-ended escalation

**Agent Decision Request**:
A stable, dependency-fingerprinted and schema-valid decision menu that names one exact criterion and question, its governed evidence and deterministic findings, and a finite ordered set of compiler-actionable choices. Returning it ends the current compilation invocation without publishing or retaining continuation state.
_Avoid_: blocked message, open-ended prompt, live continuation, heartbeat

**Agent Decision Choice**:
One compiler-authored item in an Agent Decision Request, identified by a simple stable identifier and declaring its concise meaning, predefined compiler action, expected consequence and mandatory constraints. `terminate` has the reserved meaning of stopping the run, preserving the previous valid publication and requiring a fresh compilation.
_Avoid_: free-form answer, agent-supplied parameter, validation waiver

**Agent Decision Ledger**:
A versioned, data-only set of responses supplied to a fresh compilation. Each response contains one request identifier, the request dependency fingerprint and one offered choice identifier. The compiler accepts a response only at the freshly regenerated matching request, consumes every supplied response before publication and rejects executable or free-form fields.
_Avoid_: suspended continuation, callback channel, instruction list, mutable output patch

**Challenge Finding**:
A critic's evidence-backed challenge to a proposal, classified as blocking, revision-required or advisory and displayed through a traffic-light status.
_Avoid_: comment, untracked objection

**Governance Directive**:
A versioned, scoped human instruction that becomes governed input to a later compilation without overriding mandatory network invariants.
_Avoid_: manual output edit, validation waiver, prompt pragma

**Human Intervention Request**:
A structured handoff used only when bounded agentic judgement and revision cannot resolve a material ambiguity, describing the attempted revisions, unresolved findings, missing evidence, available choices and smallest human input needed.
_Avoid_: routine approval, human review of every decision, agent failure message

**Agent Role Contract**:
A provider-neutral definition of one agent role's instructions, Evidence Packet, permitted tools, output schema, citation duties and stopping behaviour.
_Avoid_: Codex prompt, model-specific workflow

**Agent Runtime**:
The optional provider-neutral seam through which compilation submits one complete fingerprinted Agent Decision Request and accepts only its request identifier plus one offered choice identifier. Calls are lazy, single-attempt, request- and token-limited, and protected by a configurable hard wall-clock deadline. Timeout, provider failure, schema failure or an unoffered choice ends the invocation with the same non-waiting decision-required result. A concrete model provider is an Adapter at this seam; Codex is not required.
_Avoid_: embedded chatbot, Codex dependency, free-form agent call

**LCWIP Stage Decision Envelope**:
A fingerprinted, provider-neutral request binding one LCWIP stage and Agent Role
Contract to an immutable Evidence Packet, exact plan-state fingerprint, bounded
revision index and finite compiler-authored action vocabulary. Evidence content is
untrusted data, and a response can select only one offered action with governed
citations.
_Avoid_: general-purpose prompt, free-form plan edit, model-authored command

**Independent Critique Gate**:
A deterministic stage boundary that binds a separate critic's accepted decision
record to the exact primary request and tracks every material Challenge Finding to an
evidenced resolution, a permitted named-human waiver or an unresolved blocker.
Stages configured for independent critique cannot mutate authoritative state without
this gate.
_Avoid_: optional review comment, self-review, untracked objection

**Authoritative Stage Mutation**:
The immutable state transition performed only by the deterministic LCWIP compiler
after a Stage Decision Envelope, selected finite action, citations, current-state
fingerprint and any required Independent Critique Gate all validate. The mutation
surface cannot change raw evidence, policy weights, lifecycle state, representations,
mandatory waivers or adoption.
_Avoid_: agent patch, response side effect, direct lifecycle update

**No-Agent Mode**:
A deterministic execution of the same Stage Decision Envelope using its declared
fallback action without constructing or calling an Agent Runtime. It produces the
same typed review and compiler artifacts and never weakens invariants.
_Avoid_: skipped validation, empty artifact, hidden default

**Council Configuration**:
Versioned council-specific data declaring the study boundary, source locations and Criteria Set values consumed by the council-neutral compiler without changing compilation logic.
_Avoid_: council fork, hard-coded B&NES rule, deployment environment

**Area Definition**:
Versioned governed input naming one council, several councils or another coherent region whose combined study boundary, source locations and Criteria Set values are compiled without changing council-neutral logic. A Council Configuration is an Area Definition for a single council.
_Avoid_: council fork, deployment folder, map extent

**Area Deployment**:
One independently reproducible set of immutable SATN artifacts and a standalone Inspectable Review Map generated from an Area Definition. It has its own stable identity, cache namespace and deployment path and never overwrites another Area Deployment.
_Avoid_: shared output folder, council website, mutable latest map

**Deployment Catalogue**:
The lightweight index of available Area Deployments and their stable publication locations. It does not copy their governed evidence or make one Area Deployment depend on another.
_Avoid_: combined network, monolithic site bundle, B&NES landing page

**Progressive Evidence Layer**:
A published contextual or analytical layer whose size, feature count and loading state are declared before its content-addressed spatial shards are fetched for the active view. Cache absence or shard failure remains visible and never changes the compiled network.
_Avoid_: initial payload, opaque download, required browser cache

**Compiled Connection**:
The typed result of compiling one Community Connection, including its selected Alignment Option, rejected alternatives, evidence, findings, intervention coverage and provenance.
_Avoid_: drawn route, agent response

**Compilation Gate**:
The deterministic decision boundary that applies the Agent Review Policy to one explicitly governing criterion. An unselected status follows deterministic semantics; a selected status creates the same bounded Agent Decision Request for either the controlling caller or the optional direct Agent Runtime before any partial result can be published.
_Avoid_: approval screen, silent acceptance, traffic-light rollup, suspended process

**Network Compilation Unit**:
A recursively compiled subgraph assembled from Compiled Connections or smaller Network Compilation Units and assessed through the same deterministic criteria and bounded decision-menu validation protocol.
_Avoid_: map tile, administrative area

**Validated Connection**:
An immutable Compiled Connection that satisfies its applicable deterministic and agent-review contract and can be reused until a relevant governed input changes.
_Avoid_: cached guess, permanently approved route

**Criteria Set**:
A versioned collection of connection-level and network-level assessment criteria used by agent roles and deterministic validation for a compilation run.
_Avoid_: mutable scorecard, hidden rubric

**Criterion Status**:
The visible result of applying one criterion: Green when satisfied, Amber when refinement or challenge may be useful, Red when a mandatory network invariant fails, and Grey when unevaluated or evidence is unavailable. Whether it invokes an agent is controlled separately by the Agent Review Policy.
_Avoid_: aggregate score, implicit confidence, hidden failure

**Criteria Section**:
A coherent group of Criterion Statuses evaluated and displayed on its own merit. Sections are never collapsed into one overall traffic light or weighted score.
_Avoid_: dashboard total, worst-status rollup, composite score

**Full Recompile Directive**:
A Governance Directive requiring every connection and Network Compilation Unit to be compiled again under a declared Criteria Set while preserving prior results for comparison.
_Avoid_: cache clear, overwrite run

**Evidence Request**:
A structured request for evidence absent from an Evidence Packet, to be acquired and governed outside the compilation run.
_Avoid_: live browsing, unsupported assumption

**Demand Planning Pass**:
A deterministic LCWIP analytical pass that derives Origin–Destination Flows and Cycling Desire Lines, requests finite Demand Route Alternatives, assesses them under the active Guidance Profile and reconciles the result with SATN and other governed network hypotheses. It runs after the Baseline Evidence Gate and outside the SATN Wayfinding Pass; demand divergence is reported and never silently mutates SATN.
_Avoid_: Wayfinding Pass, delivery prioritisation, hidden network rewrite

**Origin–Destination Point**:
A stable, spatially inspectable origin or destination admitted from governed evidence, with an explicit study-area and equality-relevance state. A low-demand or cross-boundary point remains visible rather than disappearing from the analysis.
_Avoid_: anonymous centroid, inferred destination, filtered-out community

**Origin–Destination Flow**:
A directed quantity of trips between two Origin–Destination Points for one named Demand Scenario, retaining its unit and governed evidence identifiers. Aggregation preserves the complete input flow lineage.
_Avoid_: straight-line route, universal demand score, observed forecast

**Demand Scenario**:
A named observed, modelled or derived view of trips whose assumptions and source evidence remain explicit. Results from different scenarios are not merged as though they described the same state.
_Avoid_: hidden forecast, current-and-future blend, unversioned assumption

**Cycling Desire Line**:
A straight analytical relationship derived from one or more Origin–Destination Flows at a configured local or strategic Demand Scale. The unsimplified long list, every filter outcome and the transformation version are retained whether or not the line proceeds to routing.
_Avoid_: preferred route, SATN connection, discarded low score

**Demand Scale**:
A council-configured local or strategic distance and trip-filter context used to interpret a Cycling Desire Line. No distance rule from another authority is assumed to apply to B&NES.
_Avoid_: hard-coded 5–20 km rule, universal strategic threshold

**Demand Route Alternative**:
One of a finite set of geometry-bearing route candidates returned through the governed deterministic routing boundary for a retained Cycling Desire Line. It cites SATN Public Features or governed local/external network identifiers and is distinct from an Alignment Option selected inside SATN Wayfinding.
_Avoid_: invented agent route, final design, hidden replacement route

**Route Selection Assessment**:
A versioned, Guidance-Profile-bound comparison of finite Demand Route Alternatives across directness, gradient, safety, comfort, attractiveness and cohesion. It retains every candidate, explicit unknown, rejection reason, evidence item and bounded human, agent or deterministic decision.
_Avoid_: composite quality score, feasibility decision, unrecorded preference

**Current-Condition Assessment**:
The evidenced state of a Demand Route Alternative as it exists now. It never inherits a score from a proposed intervention or design outcome.
_Avoid_: improved route assumption, potential score, current feasibility

**Potential-Design-Outcome Assessment**:
The separately evidenced state a Demand Route Alternative might achieve after a stated conceptual intervention. It is not evidence of current conditions, detailed design, feasibility or delivery.
_Avoid_: current route quality, guaranteed improvement, scheme approval

**SATN Demand Reconciliation**:
The explicit relationship between a Demand Route Alternative and SATN Strategic Spines, Spine Access Branches, Cross-Spine Connectors or Network Gaps, or a governed local/external network. Divergence remains an inspectable finding and does not alter the SATN publication.
_Avoid_: automatic SATN correction, demand override, topology mutation

**Network Density Record**:
A scenario- and Demand-Scale-specific account of retained desire lines, preferred routes, route length, covered Origin–Destination Points and visible coverage gaps. It is analytical coverage evidence, not a priority or benefit score.
_Avoid_: investment ranking, completeness claim, hidden density target

**Demand Sensitivity Case**:
A named alternative set of distance and trip thresholds evaluated against the same unsimplified Cycling Desire Line long list. It shows which lines change without rewriting the base assumptions.
_Avoid_: silent threshold tuning, new evidence scenario, preferred answer

**Walking and Wheeling Planning Pass**:
A deterministic LCWIP analytical pass that builds walking-specific catchments, Core Walking Zone proposals, Key Walking Routes, Funnel Routes and route/area audits from governed evidence. It is independent of cycling and SATN geometry because those cannot establish footway, crossing, accessibility or lived-experience conditions.
_Avoid_: cycle-network proxy, Candidate Low-Traffic Area proxy, automated site survey

**Walking Trip Attractor**:
A stable spatial destination or origin for walking and wheeling trips, classified as a local centre, interchange, school, service, development or employment location and linked to governed evidence and explicit uncertainty.
_Avoid_: anonymous point, assumed trip generator, cycling destination proxy

**Walking Catchment**:
A configured spatial screening area around a Walking Trip Attractor with a recorded method, radius, evidence and uncertainty. Radial membership does not claim network distance or accessible-route continuity.
_Avoid_: service area without method, walkability claim, hidden distance threshold

**Core Walking Zone Proposal**:
A reviewable polygon around a local centre whose selected attractors resolve to a governed Walking Catchment. Its boundary, selection rationale, evidence, uncertainty and accountable review remain explicit.
_Avoid_: adopted boundary, low-traffic area, unreviewed buffer

**Key Walking Route**:
A walking-specific route connecting important attractors to or within a Core Walking Zone. Its geometry, selection logic and audits are governed independently of any cycling alignment.
_Avoid_: strategic cycle line, final public-realm design, inferred pavement

**Funnel Route**:
A walking-specific feeder into a Core Walking Zone, interchange, school, service or development. It retains the trip-attractor relationship and uncertainty that caused it to be reviewed.
_Avoid_: generic access branch, untraced shortcut, delivery priority

**Walking Route/Area Audit**:
A versioned Guidance-Profile-bound assessment of Core Walking Zones and walking routes across footway continuity, width, surface, crossings, gradient, severance, lighting/personal safety, seating/rest and wayfinding. Every condition records both provenance and evidence mode.
_Avoid_: browser accessibility audit, cycle-route audit, universal quality score

**Walking Audit Provenance**:
The epistemic state of an audit condition: observed, inferred, modelled or unknown. It is separate from whether evidence was gathered through desktop work, site survey or privacy-safe lived experience.
_Avoid_: inferred observation, model presented as fact, missing provenance

**Walking Site Evidence Request**:
A typed unresolved request created when a mandatory audit condition lacks the required site observation. Its presence structurally prevents a route or area from being marked Fully Audited.
_Avoid_: silent assumption, passed audit, optional note

**Walking Accessibility Need**:
An explicit need relevant to walking and wheeling evidence, including wheelchair, mobility-aid, visual, hearing, cognitive/neurodivergent, resting and personal-safety needs. These needs are planning inputs, not web-interface checks.
_Avoid_: generic accessibility flag, browser conformance, single-user proxy

**Lived-Experience Finding**:
A privacy-safe, typed thematic finding linked to governed stakeholder evidence, a walking subject and explicit accessibility needs. Public outputs require personal data to be removed and material findings require accessibility-representative review.
_Avoid_: named respondent, raw testimony, automated engagement replacement

**Walking Deficiency**:
An observed deficient or explicitly unknown walking audit condition compiled as a stable intervention input with evidence, accessibility needs and any unresolved Evidence Request.
_Avoid_: detailed scheme, priority score, unsupported defect

**Accepted Deficiency Reference**:
A mode-neutral programme boundary that cites one accepted cycling, walking/wheeling, SATN or other governed deficiency by source artifact, fingerprint and record ID while preserving its evidence, affected subject, users and accountable human acceptance.
_Avoid_: copied audit model, unsupported problem statement, anonymous gap

**Intervention Catalogue**:
A versioned, fingerprinted set of permitted strategic treatment families for route sections, junctions, crossings, area measures, supporting infrastructure, wayfinding and maintenance. Each entry defines supported geometry, modes, users, strategic scope and explicitly excluded detailed work.
_Avoid_: free-text treatment invention, product specification, unversioned menu

**Desired Design Outcome**:
An evidence-linked statement of the condition an accepted deficiency should reach, with a success measure, assumptions and explicit unknowns. It is distinct from both the deficiency and the intervention selected to pursue it.
_Avoid_: catalogue item, benefit score, guaranteed result

**Intervention Concept**:
A catalogue-bound strategic option or concept linking accepted deficiencies to Desired Design Outcomes at an approximate location, with users served, evidence, assumptions, alternatives, dependencies, exclusions, residual deficiencies and delivery status.
_Avoid_: infrastructure scheme, detailed design, construction approval

**Outline Cost Range**:
A human-verified, evidence-backed monetary interval with currency, price base, disclosed rounding, basis, confidence, included and excluded scope, quantity assumptions and unknowns. A single invented figure is not an Outline Cost Range.
_Avoid_: precise estimate, unsupported allowance, procurement bill

**Constraint Assessment**:
A typed known-clear, known-constraint, unknown or not-applicable judgement for land/highway rights, environment/heritage, utilities, traffic, dependencies, maintenance or survey/design needs. Material known judgements require human verification; unknowns remain Evidence Requests.
_Avoid_: silent constraint clearance, assumed utilities, feasibility claim

**Intervention Package**:
A machine-readable group of Intervention Concepts and Desired Design Outcomes for later strategic appraisal, including package dependencies, mutually exclusive alternatives, assumptions and residual deficiencies.
_Avoid_: prioritised programme, funding commitment, procurement lot

**Intervention Delivery Status**:
The bounded state strategic-option, concept, feasible or designed. This compiler produces strategic material; feasible or designed labels only record separately governed human evidence and never imply detailed design was performed here.
_Avoid_: inferred feasibility, automatic stage advance, adoption state

**Prioritisation Pass**:
A deterministic post-intervention analytical pass that compares council-approved scenarios and produces sensitivity-tested short-, medium- and long-term phasing. It never treats SATN validity, traffic lights or assembly order as priority evidence.
_Avoid_: Wayfinding Pass, opaque ranking, funding decision

**Approved Prioritisation Criteria**:
A versioned, fingerprinted set of measures, transforms, weights, missing-data rules and programme horizons bound to an accountable council directive. Effectiveness/benefit, policy/equality and deliverability/cost remain separately inspectable.
_Avoid_: agent-selected weight, hidden transform, validity criterion

**Analytical Programme Scenario**:
A reproducible comparison of intervention concepts under one approved set of weights and rules. Every result decomposes to raw observations, evidence, transforms, weighted contributions, view results, dependencies, cost confidence, risks and unresolved requests.
_Avoid_: recommendation, authorised programme, objective truth

**Prioritisation Sensitivity Case**:
A configured variation of approved weights or governed input observations that reports rank and phase changes against a named Analytical Programme Scenario.
_Avoid_: silent retuning, selected policy, forecast presented as fact

**Recommended Programme**:
An Analytical Programme Scenario selected by a recorded human recommendation. It remains distinct from council authorisation and does not commit funding.
_Avoid_: agent recommendation, authorised programme, funding award

**Authorised Programme**:
A previously Recommended Programme selected by a later accountable council decision. Authorisation records governance state; it is not a funding award or detailed business case.
_Avoid_: analytical scenario, automatic adoption, realised benefit

**LCWIP Governance Record**:
An immutable release-bound account of the plan sponsor, SRO, project board, decision
authorities, objectives, targets, timetable, directives, engagement, equality,
policy alignment and human lifecycle gates. It proves accountable provenance but
does not exercise democratic authority.
_Avoid_: software approval, generated mandate, informal project metadata

**Representation Source Record**:
An immutable opaque source reference and content fingerprint for one received
representation, with access and public-disposition rules, themes, position and
explicit supersession or contradiction lineage. Personal source content is not a
public artifact.
_Avoid_: rewritten submission, respondent profile, uncited sentiment

**Agent Representation Summary**:
A reproducible classification or summary that cites every included publishable
Representation Source Record and states confidence, coverage and methodology. A
named human verifies the summary and separately disposes each source; the summary
cannot decide the response.
_Avoid_: consultation decision, source replacement, uncited consensus

**Human Lifecycle Gate**:
A named, dated decision by the authority role required for one lifecycle boundary,
with rationale and evidence. Gates are cumulative: reaching an adoption state cannot
bypass scope, evidence, prioritisation, consultation, representation or equality
decisions.
_Avoid_: boolean flag, agent approval, inferred sign-off

**Equality Impact Finding**:
A source-citing record of affected users, impact, owner, EqIA process, mitigations and
resolution state. Unknown or unresolved adverse impacts block consultation and
adoption rather than being interpreted as no impact.
_Avoid_: equality score, assumed neutrality, hidden mitigation

**Policy Alignment**:
A governed link from an exact policy clause to an objective, network or intervention,
including subject evidence and a named officer's judgement. Text similarity alone is
not policy alignment.
_Avoid_: keyword match, agent interpretation, uncited policy claim

**Governance Release Fingerprint**:
The canonical digest of the substantive governance, engagement, equality and policy
content. An external adoption decision must identify this exact fingerprint, and any
post-consultation amendment records its trigger and fingerprint chain.
_Avoid_: mutable latest pointer, filename identity, adoption of unspecified content

**Wayfinding Pass**:
The compilation phase that connects Network Places into a valid end-to-end network using topology, constraints and alignment evidence. Demand and accessibility evidence do not determine connections in this pass.
_Avoid_: prioritisation, demand-led routing

**Prioritisation Pass**:
A later phase that uses demand, accessibility and other delivery evidence to order already-valid Community Connections without changing the network's required connectivity.
_Avoid_: wayfinding, network generation

**ATM Reference Corpus**:
The human-reviewed B&NES Active Travel Masterplan network, including existing and potentially planned alignments, used to improve and test the portable SATN compiler rather than define its rules.
_Avoid_: ground truth, portable dependency

**ATM-Seeded Compilation**:
A B&NES compilation that starts from ATM alignments where present and records reasons for any deviation.
_Avoid_: ATM validation, copied network

**ATM-Blind Compilation**:
A B&NES compilation that does not use ATM geometry during route proposal and compares its result with the ATM Reference Corpus afterwards.
_Avoid_: evidence-free compilation, automatic benchmark

**Divergence Record**:
The evidence-citing, red-teamed explanation of a difference between an ATM-seeded or ATM-blind result and the ATM Reference Corpus, including the attempted resolution and remaining uncertainty.
_Avoid_: geometry diff, unexplained mismatch

**Explicit Unknown**:
A material fact that the available evidence does not establish. It remains visible and must not be silently interpreted as absent, safe or zero.
_Avoid_: missing value, assumed absence

**LCWIP Guidance Profile**:
A versioned, attributable set of stable LCWIP requirement identifiers, obligations and expected evidence or artifacts, identified by issuer, document version, effective date and applicability. A later profile does not rewrite an earlier Release's recorded profile.
_Avoid_: timeless checklist, implicit DfT rule, overwritten guidance

**LCWIP Requirement Status**:
The explicit conformance state of one Guidance Profile requirement: satisfied, unknown, not-applicable, waived or failed. A waiver records a named human authority and rationale; unknown is never treated as satisfied.
_Avoid_: blank checkbox, inferred compliance, agent waiver

**LCWIP Release**:
A versioned plan record bound to one Guidance Profile fingerprint and visible lifecycle state: exploratory, evidence_incomplete, analysis_draft, consultation_draft, adoption_candidate, adopted or superseded. Every state transition has a named human gate. Adopted requires an externally recorded authorised decision and a separately evidenced, named-human verification for that same decision; automatic or generated provenance is not permitted.
_Avoid_: generated adoption, implicit approval, final draft

**Network Validity**:
Whether a proposed network satisfies its stated topology and continuity evidence rules. Network Validity is not evidence of Benefit or Priority, Feasibility, Consultation or Adoption.
_Avoid_: preferred scheme, high-priority route, approved network

**Benefit or Priority**:
The evidenced and accountable ordering of valid options for investment or delivery. It does not establish Network Validity, Feasibility, Consultation support or Adoption.
_Avoid_: compiler order, traffic-light priority, automatic programme

**Feasibility**:
The separately evidenced judgement that an intervention can be delivered within relevant physical, legal, environmental, cost and operational constraints. It does not establish Network Validity, Benefit or Priority, Consultation support or Adoption.
_Avoid_: mapped route, indicative intervention, deliverable by default

**Consultation**:
The governed engagement process that records representations, responses and accountable dispositions. Publishing a map or an analysis draft is not Consultation and Consultation does not itself establish Adoption.
_Avoid_: public web page, map access, automatic consent

**Adoption**:
An authorised external council decision recorded for a specific LCWIP Release and separately verified by a named person with distinct governed evidence. This is verification provenance, not a cryptographic signature. It is distinct from Network Validity, Benefit or Priority, Feasibility and Consultation, each of which can remain incomplete or contested.
_Avoid_: generated release, conformance result, officer draft

**LCWIP Publication Release**:
An immutable, atomic bundle of mutually validated report, web, GIS, programme,
conformance, source-quality, audit and release-history artifacts. Every artifact
carries the same release identity, lifecycle state and substantive release fingerprint.
_Avoid_: mutable export folder, independent document copies, adopted plan by filename

**Cited Material Claim**:
A public narrative assertion whose governed source records resolve through exact
citation identifiers and fingerprints. Missing evidence is published as an explicit
placeholder; it is never converted into confident prose.
_Avoid_: uncited summary, plausible generated text, hidden evidence gap

**Publication Watermark**:
The shared plan area, release ID and version, lifecycle state, evidence and
configuration fingerprints, release fingerprint and publication date embedded in
every artifact of an LCWIP Publication Release.
_Avoid_: visual logo, filename convention, latest-release pointer

**LCWIP Release Diff**:
A semantic comparison between immutable publication releases covering evidence,
method, geometry, programme, narrative and decision categories, with feature-level
spatial changes recorded separately.
_Avoid_: changelog prose, file-size comparison, geometry-only diff

**Publication Adoption Annotation**:
A later typed record of an external authorised decision and independent named-human
verification bound to the exact substantive release fingerprint. It changes
publication lifecycle metadata without rewriting the release's substantive evidence
or narrative.
_Avoid_: generated adoption statement, mutable status flag, inferred approval

**Programme Status Update**:
A dated, source-bound and confidence-stated report about exactly one delivery
dimension: design, funding, construction, completion or outcome. Only a verified
update contributes to effective status; progress in one dimension never implies
progress in another.
_Avoid_: combined RAG status, inferred completion, funding-as-delivery

**Monitoring Observation**:
A governed baseline or later measurement with method, period, unit, source, coverage,
uncertainty, observer authority and verification state. Activity observations and
outcome observations remain distinct, and neither is a causal claim.
_Avoid_: impact claim, context-free KPI, silently imputed value

**Governed Review Trigger**:
A source-citing condition such as a scheduled review, material development, network
delivery, guidance change, evidence expiry or underperformance. It creates a Review
Task and cannot silently change the historical plan or its lifecycle.
_Avoid_: automatic replanning, mutable alert flag, background plan edit

**Guidance Migration Entry**:
The explicit effect of an added, removed or changed Guidance Profile requirement on
named analyses and programme entries, with a required action. It compares immutable
profiles and does not rewrite earlier conformance.
_Avoid_: latest-guidance overwrite, generic upgrade note, inferred compliance

**Superseding Release Proposal**:
A non-adopted release proposal bound to one historical release fingerprint, a new
evidence snapshot, the current Guidance Profile and the Review Triggers that caused
it to be prepared.
_Avoid_: mutated adopted release, automatic supersession, adopted monitoring update
