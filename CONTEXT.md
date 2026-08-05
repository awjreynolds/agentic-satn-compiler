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
A primary, secondary, all-through or special education site admitted as a School Access Obligation. A college or university remains contextual evidence unless its physical campus is admitted by the active Strategic Destination Profile.
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
The default-on selected SATN layer, including its required Community and destination connections. Its line core communicates Network Display State, while its halo communicates the primary Alignment Basis; details retain every basis and the evidence behind it. It is accompanied by quiet Places and prominent material gaps and officer–compiler divergences rather than an undifferentiated red backbone.
_Avoid_: uniform-red route, hidden connectors, contextual asset inventory, colour-only meaning

**Alignment Basis**:
The evidence-backed physical or corridor basis followed by an Alignment Section, such as current cycle provision, Greenway, current or reclassified National Cycle Network, public right of way, former railway, governed Local Connector, classified road or proposed corridor. A section retains every applicable basis and names one primary basis for map presentation; basis alone never establishes condition, access, feasibility or Intervention State.
_Avoid_: route quality, intervention need, single source label, inferred suitability

**Intervention State**:
The delivery state of a selected or complementary routable section: `existing-provision`, `upgrade-required` or `proposed-new-link`. It is supported independently of Alignment Basis and does not claim cost, funding, legal authority, design readiness or deliverability.
_Avoid_: asset type, scheme status, feasibility class, unresolved gap

**Network Display State**:
The map-facing union of the three routable Intervention States and `unresolved-gap`. The core line encodes this state, while the halo encodes primary Alignment Basis; text and pattern duplicate colour meaning in the legend and details.
_Avoid_: route score, colour-only state, alignment source

**Existing and Upgradeable Assets Layer**:
An optional inventory layer containing every governed in-scope reusable asset whether selected, complementary, unselected, incomplete or topologically unconnected. An unselected existing asset retains its basis identity rather than becoming an anonymous grey alternative.
_Avoid_: selected network, hidden asset loss, feasibility inventory

**Unselected Candidates Layer**:
An optional layer containing finite compiler-authored candidates not selected into the active Scenario Compilation. Ordinary alternatives may be muted, but existing-asset identity and Material Officer–Compiler Divergence styling remain distinct and inspectable.
_Avoid_: discarded geometry, deleted options, grey divergence

**Backbone-Outward Assembly**:
The iterative formation of a Backbone-and-Access Network from all Strategic Spines concurrently, extending through the nearest reachable unserved Access Obligations and joining differently rooted branches where they first meet. It ends with every Access Obligation served or exposed as a Network Gap.
_Avoid_: one-spine-at-a-time build, global pairwise routing, order-dependent catchment

**Access Obligation**:
A Community, School or Strategic Destination Site that must be served by a bounded connection into the strategic network without requiring a peer-to-peer route. A degree-one Access Obligation is valid once its applicable access rule is satisfied.
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
One evidence-backed, end-to-end way of realising a Community Connection. It may follow one source corridor or form a continuous composite from compatible Parallel Alignment Sections. Only one Alignment Option may be selected into a published network.
_Avoid_: parallel connection, final design

**Alignment Candidate Set**:
The finite, evidence-backed Alignment Options generated for one strategic Community Connection before substitute/complementary classification and selection.
_Avoid_: unconstrained route search, alternative network

**Evidence Observation**:
One atomic, claim-specific statement from a governed Source Export, retaining publisher, source family, stable publisher key where governed, observation/effective dates, licence, coverage, raw-content SHA-256, canonical evidence-geometry fingerprint and explicit missing, stale or conflict state. An observation supports only the claim it names.
_Avoid_: dataset confidence score, uncited tag, inferred condition, mutable fact

**Evidence Source Catalogue**:
The deterministic governed register of provider capabilities, source families, spatial coverage, effective dates, licences, acquisition and normalisation contracts, permitted uses and configured authority ranks. It selects eligible sources for an Area Evidence Scope but does not download evidence, let an agent admit a source or treat catalogue order as authority.
_Avoid_: live source search, deployment-specific adapter list, latest-data lookup, agent-selected evidence

**Area Evidence Scope**:
The stable spatial-partition set resolved from an Area Definition boundary for evidence-source selection. It may cross administrative or national boundaries; applicable sources are matched by capability and coverage, while uncovered partitions create Evidence Requests without preventing compilation.
_Avoid_: council name, jurisdiction switch, source footprint, compilation failure boundary

**Governed Asset Record**:
The stable compiler record for one physical reusable asset assembled from Evidence Observations under a configured claim-specific authority hierarchy. Conflicting observations remain attached; they are not collapsed into an unsupported best answer, and unavailable optional evidence never removes the asset.
_Avoid_: source feature copy, verified condition, globally preferred dataset

**Asset Accounting**:
The exhaustive result record for every governed asset, keeping scope state, opportunity state and evidence states independent from zero or more Candidate Participation records. An in-scope asset with no candidate participation carries an explicit non-participation reason.
_Avoid_: selected-assets list, global candidate disposition, silent omission

**Candidate Participation**:
One asset's role in one exact `(candidate_set_id, candidate_id)` context: selected contributor, complementary contributor, eligible not selected, ineligible, officer excluded, incomplete evidence or topology unconnected. The same physical section may participate differently in several candidates without acquiring one contradictory global outcome.
_Avoid_: asset-wide winner state, candidate membership flag, deleted alternative

**Selection Disposition**:
The exact outcome for one candidate participation, including its structured reason, failed rule or selection provenance. Every participation has exactly one disposition; candidate-set context is part of its identity.
_Avoid_: global section status, free-text rejection, implicit non-selection

**Constraint Assessment**:
A claim-specific supported, contradicted or unknown assessment of access, condition, land, continuity, protected space or another configured constraint. Unknown, stale and conflicting evidence stays unknown and can create an Evidence Request; it is never converted to safe, absent, favourable or automatically ineligible.
_Avoid_: confidence score, missing-means-clear, hidden veto

**Local Connector Evidence**:
A governed local assertion for a reusable link not adequately represented by the configured source hierarchy, with stable identity, geometry, source, author or responsible role, observation date, verification state and provenance. It may preserve local knowledge or a newly built link but cannot invent routing continuity or legal access.
_Avoid_: freehand route, uncited officer note, automatic network edge

**Evidence Request**:
A structured, queued request for a missing or conflicted claim, naming its affected asset/candidate, required evidence kind and deterministic consequence. Requests are handled outside compilation; they never cause a valid-input compile to wait or an agent to fetch raw facts.
_Avoid_: interactive blocker, agent web search, missing-is-zero

**Governed External Analysis Run**:
An immutable evidence-producing execution of a named external analytical engine against pinned source exports and a declared profile or scenario. Its record retains the engine version and licence, every input identity and content hash, configuration, deterministic or seeded execution details, result schema and hash, coverage and limitations. The compiler may admit its result as Evidence Observations, but never calls the engine live during compilation or treats its route, accessibility, assignment or simulation output as canonical geometry or policy.
_Avoid_: live analysis dependency, external route authority, opaque model score, agent tool call

**Officer Scenario Authoring**:
A review interaction in which an officer assembles compiler-authored sections, candidates or offered actions into a named scenario and exports a typed Officer Decision Ledger for a fresh compilation. It cannot create authoritative freehand geometry, silently remap stale identities or mutate the published baseline; the compiler validates and applies every choice and retains any officer–compiler divergence.
_Avoid_: map editing, adopted network, mutable compiler state, free-form route override

**Network Diagnostic Profile**:
A frozen, fingerprinted configuration for deterministic graph and route diagnostics such as connectivity, degree, dangles, circuity, directness, mesh, reachability or severance. Diagnostics explain topology, gaps and option consequences without repairing geometry, establishing legal access or becoming an unexplained route score; unavailable inputs remain explicit limitations or Evidence Requests.
_Avoid_: route policy, automatic topology repair, legal-access inference, hidden network score

**Alignment Choice Point**:
A governed topological point that bounds a real corridor choice: an Alignment Option endpoint, a usable divergence or rejoining point, or a connector or crossing where a continuous selected alignment could switch between alternatives. An ordinary intermediate RoadGraph node, evidence change or access attachment is not a choice point unless it changes that switchability.
_Avoid_: every graph node, visual crossing, evidence-section boundary

**Parallel Alignment Section**:
A maximal continuous chain between two Alignment Choice Points, potentially spanning many RoadGraph nodes and several places, compared with nearby chains that perform the same strategic continuity role. Sections are coalesced by unchanged corridor membership and switchability rather than a minimum length, so a short genuine divergence remains visible. Sections may be selected in different combinations to form continuous end-to-end Alignment Options; proximity alone does not make them substitutes.
_Avoid_: arbitrary source edge, adjacent-node fragment, minimum-length cutoff, whole-corridor alternative, topology-free shortcut

**Parallel Alignment Candidate Set**:
The finite, order-independent group of every same-role, boundary-equivalent Parallel Alignment Section connected by the qualifying pairwise relationship in the Parallel Candidate Proximity Profile. Two outer members need not qualify directly when an intermediate member connects them into the same corridor family. Singular Parallel Selection chooses once across the whole group and retains every other valid member as a Non-Selected Parallel Alignment; it never creates overlapping or order-sensitive pairwise tournaments.
_Avoid_: two-route-only comparison, sequential elimination, overlapping decision sets, hidden discarded candidate

**Parallel Candidate Proximity Profile**:
A frozen, versioned discovery rule declaring the scope-sensitive distance and minimum symmetric coverage needed for two continuous chains to become parallel candidates. Each span uses its governed `network_scope`; distances, each-way coverage and unresolved-scope brackets are configurable and fingerprinted, while two-way symmetry, scope boundaries remaining non-topological, and proximity never establishing substitution are fixed method safeguards.
_Avoid_: nearest-point match, one-sided overlap, Dutch standard, automatic substitution

**Symmetric Parallel Coverage**:
The length percentage of each continuous chain lying within its locally configured projected distance of the other chain, calculated independently in both directions. A pair qualifies only when both percentages meet the active Parallel Candidate Proximity Profile's minimum; one short nearby overlap cannot qualify a much longer chain.
_Avoid_: nearest-point distance, one-way buffer match, visual overlap estimate

**Scope-Sensitive Parallel Candidate**:
A parallel-candidate relation whose result depends on an unresolved `network_scope` span tested at both the urban and rural proximity thresholds. A wider-only match remains visible as scope-sensitive evidence so a plausible comparison is not lost; it neither resolves the missing scope nor establishes substitution.
_Avoid_: assumed rural scope, hidden fallback, automatic substitute

**Bounded Parallel Candidate Generation**:
The deterministic formation of a finite menu from every compiler-authored end-to-end base alignment plus only those continuous hybrids that switch at proven Alignment Choice Points to obtain a material evidenced population, topography, access or existing-infrastructure advantage. Equivalent and wholly dominated routes are removed under stable configurable bounds, while valid routes excluded from the action menu remain inspectable; arbitrary RoadGraph paths, every possible section combination and agent-invented corridors are never generated.
_Avoid_: all-path search, Cartesian hybrid expansion, qualitative route invention, hidden discarded candidate

**Singular Parallel Selection**:
The Backbone-and-Access rule that a same-role, boundary-equivalent set of substitute Parallel Alignment Sections contributes exactly one selected section to the Strategic Spine direction. Rejected substitutes remain inspectable evidence, while local places and access obligations are served through bounded access branches or exposed as Network Gaps; multiple strategic sections remain only when distinct complementary roles are established.
_Avoid_: parallel backbone bundle, duplicate strategic spine, access branch promoted by default, cost claim

**Non-Selected Parallel Alignment**:
A valid substitute Alignment Option that Singular Parallel Selection did not choose for the generated strategic network. It retains its geometry, evidence, comparison reason, decision provenance and change conditions and remains visibly inspectable as a muted alternative; non-selection is not a finding that the route is bad, invalid or unavailable to a later governed scenario.
_Avoid_: deleted route, invalid route, hidden loser, abandoned scheme

**Composite Alignment Continuity**:
The requirement that consecutive selected Parallel Alignment Sections meet at the same governed Alignment Choice Point through a continuous, bidirectionally traversable connection. A missing bridge, crossing or link remains a Network Gap with any applicable Intervention Archetype; proximity or an indicative intervention cannot silently join the sections or make the network Complete.
_Avoid_: geometric snap, inferred bridge, topology-free composite

**Alignment Transition**:
One switch between distinct competing corridor chains at an Alignment Choice Point. Changes of RoadGraph edge, source feature, road name or road class along the same continuous corridor are not transitions; leaving one corridor for a parallel chain and later rejoining creates two.
_Avoid_: graph-edge boundary, road-class change, evidence-section boundary

**Strategic Route Coherence**:
The inspectable continuity of one end-to-end Alignment Option across its selected Parallel Alignment Sections and corridor transitions. When population, topography, access and existing-alignment evidence are materially equivalent, the option with fewer unnecessary corridor transitions is preferred; a transition remains valid when a material evidenced advantage justifies it.
_Avoid_: no-hybrid rule, hidden transition penalty, shortest-edge-chain assumption

**Material Alignment Ambiguity**:
A choice between at least two eligible Alignment Options whose governed evidence gives them conflicting material advantages, so no option is materially no worse across national active-travel guidance, population, topography, access and existing infrastructure. Only this conflict warrants an Agent Runtime choice; a sole option, a materially dominant option, near-equivalence or merely missing evidence is resolved deterministically with limitations retained.
_Avoid_: any multi-option choice, missing-evidence escalation, routine agent confirmation, hidden preference score

**National Active Travel Guidance Evidence**:
The versioned, citation-backed assessment of available Alignment Option facts against the applicable principles and considerations in Cycle Infrastructure Design (Local Transport Note 1/20) and Active Travel England's Rural Design Guide. It keeps each consideration separate as supported, contradicted or unassessed evidence for agent and fallback judgement; a material departure must be explained and retained as an intervention need or change condition, but guidance is neither a veto nor a minimum requirement and the assessment cannot certify detailed route compliance.
_Avoid_: LTN, LTN score, national standard score, compliance certificate, assumed design quality, mandatory minimum, guidance veto

**Deterministic Alignment Fallback**:
The reproducible selection of one eligible Alignment Option by the active Network Selection Profile's declared hierarchy, with a stable identifier used only as the final tie-break. The same fallback resolves near-equivalence and every unavailable, failed or invalid Agent Runtime response without retrying or waiting for human input, while its triggering reason remains explicit in the Agent Decision Record.
_Avoid_: emergency preference, runtime retry loop, interactive pause, silent default

**Ordered Parallel Alignment Resolution**:
The stable network-order resolution of one Parallel Alignment Candidate Set at a time, applying and validating each choice before regenerating any affected downstream set. Each set receives its own small request; reaching the configured agent-call bound or losing the provider applies the Deterministic Alignment Fallback to every remaining set so compilation still completes.
_Avoid_: network-wide mega-request, cross-set Cartesian choice, stale downstream menu, compilation pause

**Alignment Resolution Completion Guarantee**:
The requirement that a compilation started from valid governed inputs resolves every Parallel Alignment Candidate Set to a selected alignment or explicit Network Gap, regardless of ambiguity, missing comparison evidence or Agent Runtime failure. An unreadable, malformed or unverifiable governed input instead produces a terminal failure record and leaves the previous valid publication untouched; the guarantee never permits invented geometry, evidence or authority.
_Avoid_: best-effort compilation, agent-dependent completion, fabricated recovery, invalid-input publication

**Parallel-Reduction Proving Corpus**:
The smallest governed synthetic corpus whose exact inputs and expected artifacts demonstrate the required discovery, selection, fallback, gap and divergence behaviour of parallel-route reduction. It contains one composite acceptance scenario covering every representative example in a single compilation plus independent deep scenarios for exact boundary diagnosis; named real geography is not an acceptance authority for the feature.
_Avoid_: live-area benchmark, Bath–Saltford acceptance anchor, production dataset, real-geography proof

**Parallel-Reduction Corpus Gate**:
The two-level CI contract for the Parallel-Reduction Proving Corpus: the single light acceptance compilation runs on every pull request, while the full deep suite runs when parallel-reduction compiler or configuration contracts change, before release and by explicit manual trigger. Both levels use the supported production compiler seam and checked-in expected results; review-map publication and helper-only tests are outside the gate.
_Avoid_: full deep suite on every change, publication test, manual demonstration, unit-test-only proof, live-data smoke test

**Parallel-Reduction Acceptance Suite**:
One fast composite synthetic scenario that includes every representative parallel-reduction example in clearly named zones separated beyond the maximum configured rural candidate distance, demonstrating end-to-end discovery, resolution and result generation in a single routine CI compilation. It has one checked-in expected result, remains spatially legible for human inspection and favours broad contract coverage over exhaustive threshold combinations.
_Avoid_: several routine compilations, smoke test without assertions, full boundary matrix, helper-only suite

**Parallel-Reduction Deep Suite**:
The complete decision-boundary suite used to validate parallel-route reduction at governed key checkpoints, covering values immediately below, at and above each threshold plus only those multi-factor interactions capable of changing an outcome. It also covers source-order invariance, missing evidence, Agent Runtime response classes and deterministic reruns without enumerating every possible combination of unrelated settings.
_Avoid_: every-possible-combination matrix, routine fast suite, live-area benchmark, optional ungoverned test collection

**Parallel-Reduction Expected Result**:
A checked-in canonical result artifact declaring the exact closed roster of Scenario Compilation candidate, selection, retained-alternative, decision, gap and divergence records expected from one governed synthetic corpus scenario; any unexpected addition, omission or change fails comparison even when the selected route is unchanged. It excludes environment-dependent paths, timings, timestamps, usage, live-model identity and generated prose, and contains no review-map or other publication contract.
_Avoid_: HTML snapshot, live-model output snapshot, public-facing assertion, hand-inspected result, helper return value

**Parallel-Reduction Scenario Manifest**:
A checked-in data-only declaration of one synthetic scenario or named acceptance zone, binding its metric geometry, governed evidence, every active configurable value, resulting profile fingerprint, scripted runtime behaviour and stable expected-result identity. A shared deterministic builder may assemble zone manifests into the single composite acceptance compilation without hiding their individual boundaries.
_Avoid_: bespoke Python fixture, live source download, inline test geometry, mutable scenario builder

**Scripted Corpus Runtime**:
A deterministic Agent Runtime test adapter that returns a configured valid choice, timeout, provider failure or invalid response for each synthetic corpus request. It proves request validation, responder provenance and fallback behaviour without calling or binding expected results to a changing live AI model.
_Avoid_: live-model acceptance test, fake planning evidence, model-specific golden answer, generated rationale snapshot

**Parallel-Reduction Reference Regeneration**:
The explicit developer action that recompiles governed corpus fixtures and proposes new checked-in Parallel-Reduction Expected Results for semantic review. CI treats existing expected results as read-only and never accepts or regenerates changed answers automatically.
_Avoid_: automatic golden update, CI self-approval, unreviewed snapshot refresh, live-result recording

**Parallel-Reduction Visual Reference**:
A non-authoritative checked-in overview of the composite acceptance scenario's named synthetic zones and expected selected, retained and gap geometry, used only to make the fixture understandable to a human. CI does not use screenshot comparison or review-map publication as its result oracle.
_Avoid_: golden screenshot, publication assertion, visual-only acceptance, map regression gate

**Preferred Strategic Alignment**:
The one selected Alignment Option for a substitute Alignment Candidate Set under a declared Network Selection Profile, with rejected alternatives and change conditions retained for inspection. It is not final design, a safety finding, feasibility evidence or a funding decision.
_Avoid_: objectively correct route, scheme approval, preferred scheme

**Network Selection Profile**:
A frozen, fingerprinted, data-only local policy declaration that orders every supported Candidate Reuse Class and Intervention State, declares a lexicographic comparator, material-difference and displacement rules, unknown-value behaviour, optional traffic profile, deterministic final tie-break and bounded agent calls. Council order and thresholds are supplied by configuration rather than embedded in compiler code.
_Avoid_: hidden score, hard-coded council preference, agent policy, mutable setting

**Candidate Reuse Class**:
The evidence-derived selection tier assigned to an eligible Alignment Option by the active profile: `existing-cycle-provision`, `upgradeable-off-carriageway`, `low-traffic-non-a-road` or `a-road-major-protected-infrastructure` in the starter profile. Every supported class appears exactly once in configured order; no candidate receives an implicit class or status-only advantage.
_Avoid_: Intervention State, road hierarchy, automatic asset winner, unconfigured fallback

**Material Displacement Reason**:
A machine-readable, profile-versioned finding required when a lower-ranked Candidate Reuse Class displaces a higher-ranked eligible candidate. It identifies selected and displaced candidates, reason code, observed values, configured threshold, governed evidence identifiers, profile fingerprint and decision provenance; being shorter, being an A road or lacking optional evidence is not sufficient.
_Avoid_: free-text rationale, weighted-score difference, unexplained exception

**Population Reach Profile**:
The governed whole-Output-Area measure of residents whose population-weighted centroids lie within a declared straight-line corridor around one complete end-to-end Alignment Option, with its source date, configurable radii and sensitivity retained. The measure belongs to the whole option even when its geometry is rendered as several line sections; those sections do not independently redefine or subdivide its population reach. It is not predicted demand, accessible population or a walking-time claim.
_Avoid_: segment population, five-minute catchment, demand model, connected homes

**Section Population Capture**:
A governed local map measure of the whole-Output-Area residents whose population-weighted centroids lie within a declared straight-line corridor around one population display section of the strategic network. It describes the population passed by that part of the network, not an end-to-end journey or the number of people expected to cycle the complete route. Every resident covered by governed evidence counts regardless of which side of the commissioning authority's boundary they live on; Area Definition membership is retained as an inside/outside breakdown for inspection rather than used as a demand cut-off. Each Output Area is counted once within a section but may legitimately appear in the evidence for neighbouring sections; displayed section values must not be summed without a separate geometry-level deduplication step. A map user may select several sections to obtain a deduplicated population capture for their combined geometry, but that is an exploratory view rather than a new governed corridor or travel claim.
_Avoid_: end-to-end ridership, expected trip length, additive segment totals, connected population

**Population Display Section**:
A deterministic subdivision of selected or non-selected strategic alignment geometry used to calculate and colour Section Population Capture. Its along-route display length is configurable independently of the lateral population-capture radius: the default is 100 metres and the permitted maximum is one kilometre. A section is shortened where the governed geometry ends, reaches an Alignment Choice Point or crosses a governed `network_scope` boundary at which its population-capture radius changes. Display cuts are evidence-rendering boundaries only and do not become graph nodes, Alignment Choice Points or Alignment Transitions.
_Avoid_: arbitrary graph edge, new routing node, strategic corridor boundary, journey-length assumption

**Population Capture Radius Profile**:
The frozen declaration of the lateral straight-line selection radius used for Section Population Capture in each governed `network_scope`. The initial explicit values are 250 metres for urban sections and 750 metres for rural sections, reflecting half of the initial 500-metre urban and 1,500-metre rural parallel-candidate distances without being calculated from them automatically. The selecting agent receives only these frozen scenario values. Optional wider radii may be precompiled as labelled exploratory inspection layers, but switching layers does not reroute the compiled scenario; changing a selection radius requires a new compilation and decision record.
_Avoid_: display-section length, walking-time claim, derived half-distance, dynamic rerouting, hidden radius

**Candidate Population Trace**:
The ordered sequence of Section Population Capture observations supplied for one parallel candidate, retaining each 100-metre display section's position, governed `network_scope`, applied selection radius and resident count so the selecting agent can see where population rises and falls. It does not collapse a long corridor into an end-to-end population or journey claim. Any deterministic compression must preserve the locations and lengths of sustained differences between candidates.
_Avoid_: forty-mile population total, expected riders, unordered histogram, colour-only evidence

**Material Population Difference**:
A deterministic evidence flag indicating that corresponding local portions of one parallel candidate capture at least 500 more residents and at least 50% more residents than the alternative for a continuous corridor distance of at least 500 metres. The absolute, relative and persistence thresholds are frozen configurable profile values. The flag helps the selecting agent locate a sustained difference but does not select a route, suppress shorter observations or replace the complete Candidate Population Trace.
_Avoid_: automatic population winner, percentage-only difference, isolated 100-metre spike, hidden weighted score

**Population Capture Colour Scale**:
A deterministic presentation scale calculated from the complete compiled scenario's Section Population Capture values for the active inspection layer, never from the current viewport. It may adapt its numeric breaks so rural-only and mixed urban/rural maps remain legible, but it must display the resulting numeric legend and must not be supplied to the selecting agent in place of resident counts. Exact palette and class-count calibration belong to review-map prototyping.
_Avoid_: viewport rescaling, colour as decision evidence, hidden legend, cross-scenario colour claim

**Education Access Profile**:
The frozen, evidence-bounded declaration of which education-access measures may inform Alignment Option comparison, including the independent-travel phases and the boundary between school-register and supplementary evidence. It does not create a school safety finding, travel-demand model or access guarantee.
_Avoid_: school-safety policy, school-mode-share model, accessibility guarantee

**Independent-Travel Opportunity**:
A separate, evidence-bounded comparison of how an Alignment Option may support secondary-age or all-through secondary education access under declared measures. It does not claim that a route is safe, suitable or independently accessible.
_Avoid_: school safety verdict, accessibility guarantee, demand score

**Existing-Alignment Advantage**:
A bounded selection advantage based on separately recorded recognised-corridor, reusable-asset or delivery evidence. Current route status alone does not establish condition, legal access, low cost or feasibility; declassified status alone receives no advantage.
_Avoid_: existing-route preference, assumed cheapness, feasibility finding

**Strategic Destination Profile**:
A frozen, versioned declaration of the governed physical-site classes that automatically become Strategic Destination Access Obligations, together with their evidence and usable-access-point requirements. Its initial mandatory classes are further- and higher-education campuses and acute, general and major community hospitals; lower-order amenities remain contextual unless another declared class admits them.
_Avoid_: undifferentiated amenity layer, hidden destination list, individual building promotion

**Strategic Destination Site**:
One governed physical campus or hospital site that qualifies under the active Strategic Destination Profile, independent of the organisation that owns or operates it. Each site creates one obligation and may have several usable access points; separate sites remain separate obligations, while individual buildings within one site do not multiply them.
_Avoid_: provider organisation, institution-wide obligation, individual destination building

**Provisional Strategic Destination Site**:
A physical campus or hospital site admitted from pinned OSM evidence matching a qualifying profile class when official site-register coverage is unavailable. It creates the same access obligation while keeping its provisional classification and source visible; later official evidence takes precedence without rewriting the earlier Scenario Compilation.
_Avoid_: verified official site, hidden OSM fallback, provider-routed place classification

**Provider-Routed Site Access**:
A dated evidence observation that a named pedestrian-routing provider returned a route into a Strategic Destination Site using the site's stable provider identity. It supports provisional strategic access without claiming that the entrance is surveyed, legally public, cycle-permitted, accessible or permanently open.
_Avoid_: inferred entrance, verified entrance, legal access, route survey

**Strategic Destination Access Obligation**:
A requirement for each Strategic Destination Site admitted by the active profile to have at least one topological connection from a usable site access point into any continuously connected part of the selected strategic network. One connection serves the whole physical site regardless of its size or other entrances; missing access evidence never removes the obligation, and unresolved access is published as a prominent Network Gap rather than stopping compilation.
_Avoid_: proximity-only service claim, direct-spine requirement, mandatory backbone waypoint, alignment veto

**Strategic Destination Access Connection**:
A bounded physical connector from a mapped or provider-routed entrance on the boundary of one Strategic Destination Site into the selected strategic network. Reaching that entrance serves the strategic obligation without routing through the site; a continuous pedestrian route, footway, shared path or public right of way may make it provisionally served while unknown or restricted cycling access remains an explicit intervention need.
_Avoid_: internal campus route, mandatory backbone waypoint, proximity link, surveyed cycle route, detailed access design

**Strategic Education Destination**:
A governed physical further- or higher-education campus admitted automatically as a Strategic Destination Access Obligation by the active Strategic Destination Profile. An institution with several campuses creates separate site obligations; its individual academic buildings do not.
_Avoid_: discretionary campus admission, every education building, School Access Obligation

**Strategic Healthcare Destination**:
A governed physical acute, general or major community hospital admitted automatically as a Strategic Destination Access Obligation by the active Strategic Destination Profile. GP surgeries, pharmacies and small clinics remain contextual unless another declared destination class admits them.
_Avoid_: undifferentiated healthcare amenity, every clinical service, proximity-only service claim

**Scenario Compilation**:
One immutable compilation defined by an Area Definition, evidence snapshot, Criteria Set, Network Selection Profile and accepted decisions. It may be compared with other scenarios but does not itself create authority for a Reference SATN.
_Avoid_: mutable scenario, adopted network, live policy view

**Generated Scenario Compilation**:
A publishable Scenario Compilation whose alignment choices were resolved by deterministic compiler rules, a validated Agent Runtime response or the Deterministic Alignment Fallback, without an Officer Decision or formal Adoption. Its public provenance identifies how each choice was resolved, but grants no human planning authority.
_Avoid_: generated clean baseline, agent-approved network, Officer-Informed Scenario Compilation, Reference SATN

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
A new immutable Scenario Compilation containing at least one attributable accepted human decision applied only to its exact target, while other choices may retain deterministic, agent or fallback provenance. It is compared with rather than silently substituted for its Baseline Scenario Compilation, and its deployment-level label does not imply that an officer reviewed every choice; public metadata need not expose personal details beyond the accountable role.
_Avoid_: mutable override layer, hidden exception, rewritten baseline

**Officer Decision**:
A frozen, versioned and attributable human planning decision bound to one stable logical compiler target rather than an exact geometry fingerprint. It records the responsible officer and role, date, rationale, governed evidence, source and status without embedding geometry in free text; it remains in force until explicitly superseded or withdrawn, while ordinary geometry corrections, changed evidence or a changed Network Selection Profile may create a Material Officer–Compiler Divergence but never make the decision expire or silently cease to apply.
_Avoid_: agent decision, expiring decision, stale decision, Area Definition exception, hidden override, edited evidence

**Material Officer–Compiler Divergence**:
An explicit finding that a current valid Officer Decision selects a different eligible alignment from the compiler's current evidence-preferred option. The officer-selected route remains the primary route in that Officer-Informed Scenario Compilation, while the compiler-preferred alternative is highlighted distinctly rather than muted as an ordinary rejected option; the finding does not declare either route objectively correct.
_Avoid_: officer error, correct route, grey rejected alternative, silent override

**Officer Decision Target Unavailable**:
An explicit governance finding that the stable logical target of a continuing Officer Decision no longer exists among the current materially equivalent compiler targets. Generation still completes, but the decision is neither expired nor silently transferred to a different route and remains visible until an authorised human supersedes or withdraws it; when no other Officer Decision applies, the new output is a Generated Scenario Compilation with an unresolved-decision warning rather than an Officer-Informed Scenario Compilation.
_Avoid_: stale decision, automatic remapping, nearest-route substitution, silent expiry

**Officer Decision Ledger**:
The immutable canonical set of Officer Decisions supplied as initial governed input to generation. Every applicable decision controls its stable logical target, all other targets resolve normally, and a non-empty applied ledger creates an Officer-Informed Scenario Compilation; changed evidence or profile does not expire a decision and instead may produce a highlighted Material Officer–Compiler Divergence.
_Avoid_: interactive approval gate, mutable configuration, expiring decision ledger, second network source, agent ledger, output patch

**Human Intervention Response**:
A frozen, versioned human answer to one exact current Human Intervention Request, selecting one compiler-offered action and retaining its request, baseline, evidence and profile lineage. An accepted response is translated into the Officer Decision Ledger rather than creating another mutation path.
_Avoid_: free-form instruction, invented action, agent response, suspended compilation

**Deployment Authority State**:
The explicit public status of one deployment output as a Baseline Scenario Compilation, Generated Scenario Compilation, Officer-Informed Scenario Compilation or formally adopted Reference SATN. Compiler rules, Agent Runtime activity, Officer Decisions and formal Adoption remain separately labelled authority records; an agent-selected or fallback-selected output has generated authority only, and a development or fake runtime cannot substantiate an officer-review or adoption claim.
_Avoid_: generic published network, implied officer approval, agent adoption

**Network Gap**:
An explicit non-routable result feature between governed endpoints where no continuous, bidirectionally traversable evidenced candidate exists. It carries `unresolved-gap` Network Display State and applicable Evidence Requests, allows generation to complete as a Reviewable Network, and prevents the network being Complete without pretending that indicative geometry is a route.
_Avoid_: visual gap, omitted link, proposed new link, routable candidate geometry

**Route Refinement Finding**:
A recorded defect in an Alignment Option, such as a discontinuity, invalid join, excessive detour, or uncovered intervention need, that must be repaired or become a Network Gap.
_Avoid_: automatic snap, hidden error

**Crossing Warning**:
A non-blocking indication that route geometries intersect visually without a shared Junction Node. It creates no Alignment Choice Point, connection or hybrid transition and invites inspection without implying that the crossing must connect.
_Avoid_: topology failure, automatic junction, inferred hybrid, prohibited crossing

**Quiet Lane**:
A rural lane whose low motor-traffic conditions and treatment make it a plausible active-travel alignment; the term does not imply that through motor traffic is prohibited.
_Avoid_: traffic-free lane, access-only lane

**Access-Only Quiet Lane**:
A rural lane where through motor traffic is physically or legally filtered while authorised access, including landowner, property and emergency access, remains. Governed filter evidence keeps it eligible as positive alignment evidence and prefers it to an otherwise comparable unfiltered through-traffic village street without making it an automatic winner over materially different options.
_Avoid_: Quiet Lane, traffic-free path, route excluded by its filter, unconditional preferred route

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
A highest-priority requirement for a rural School to have a valid Spine Access Connection, or for an urban School's usable entrance to connect through continuous Low-Traffic Area street or path fabric to a portal on an Urban Main-Road Spine. It requires a real topological connection from the School Access Point into the selected strategic network but does not require the strategic alignment itself to pass the School. An unresolved connection is published as a prominent Network Gap for investigation rather than making every otherwise reviewable alignment ineligible or stopping compilation. It does not create a route to a Community or another School, and an urban residential-street centreline is not asserted.
_Avoid_: School Network Place, mandatory backbone waypoint, alignment veto, school-to-school route, school-to-Community route

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

**Topography Evidence Threshold**:
An adjustable condition that flags potentially material climbing evidence when an edge contains a Steep Gradient Section for at least 100 metres, a Very Steep section for at least 50 metres, a Severe section for at least 30 metres, or repeated shorter climbs whose cumulative ascent makes the route materially harder. The flag may be supplied to an agent comparing already-identified Alignment Options, but it neither initiates a route decision nor rejects or selects an option by itself.
_Avoid_: automatic alternative trigger, gradient prohibition, invisible routing weight, elevation score

**Topography Profile**:
The distance and per-direction cumulative-ascent, cumulative-descent, Gradient Sections and steepest-sustained-gradient evidence displayed for an Alignment Option to support human and agent judgement. It is derived from elevation throughout the alignment rather than endpoint difference, and the measures are not collapsed into a composite effort score.
_Avoid_: net elevation change, cycling effort score, hidden weighting

**Cumulative Elevation Variation**:
The direction-independent sum of cumulative ascent and cumulative descent along a governed elevation profile, expressed as one positive measure for comparing Parallel Alignment Sections and their composed Alignment Options. A composed option adds its ordered sections only when every section has complete, compatible evidence; the raw total is not normalised by route length, while distance and directional Gradient Sections remain separately inspectable.
_Avoid_: total elevation change, net elevation change, elevation per kilometre, elevation score, cycling effort

**Material Cumulative Elevation Difference**:
A symmetric evidence flag showing that two complete Cumulative Elevation Variation measures differ by at least 20 metres and by at least 25% of the larger value. Its frozen configurable thresholds identify a potentially meaningful contrast for judgement without selecting an alignment or hiding the underlying measures.
_Avoid_: topography winner, elevation score, automatic route choice, directional effort

**Micro-Gradient Interval**:
A distance-aligned 20 metre detail or 50 metre overview measurement derived from governed elevation samples no more than 12.5 metres apart. It records direction, severity, supporting evidence and uncertainty; unavailable evidence remains explicit rather than being interpreted as level ground.
_Avoid_: map-tile gradient, assumed flat interval, endpoint-only slope

**Gradient Inspection Path**:
An ordered, continuous selection of eligible Published Features assembled from one active endpoint for exploratory analysis. Aggregate Cross-Spine Connector geometry is excluded because its constituent edges already carry the analytical evidence.
_Avoid_: arbitrary multi-selection, disconnected edge set, aggregate double-counting

**Review Lens**:
A compact map-anchored evidence surface. Pointer hover or keyboard focus previews high-level evidence for a visible map artifact; click or touch pins it, and selecting a second comparable line segment opens an exploratory two-segment comparison with raw values and a display-only spider chart. Empty map space or the close control dismisses it. Missing evidence remains Unknown and every full record remains inspectable without turning the lens into a score or a permanent panel.
_Avoid_: persistent evidence panel, stale selection, canvas-only popup, composite route score

**Segment Comparison**:
An exploratory comparison of two explicitly selected line segments in the Review Lens. It presents available evidence dimensions in a raw-value table and normalises only shared dimensions for a spider chart whose outward direction is consistently more favourable. The comparison does not change governed selections, and unavailable evidence is Unknown rather than zero.
_Avoid_: automatic winner, hidden weighting, unknown-as-zero, route-selection input

**Linear Evidence View**:
A specialised shared-distance view opened deliberately from a pinned Review Lens when a Gradient Inspection Path is active. It aligns Micro-Gradient Intervals with road classification and future engineering evidence tracks. Reversing the path reverses directional gradient without changing the governed source evidence.
_Avoid_: independent charts, edge-only summary, composite route score

**Contextual Terrain Mode**:
An optional visually exaggerated 3D terrain view used for orientation. It is never an analytical elevation source, and failure of its replaceable raster-dem provider restores the default 2D map without affecting the network or Linear Evidence view.
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

**DfT Motor-Traffic Evidence**:
An optional governed observation matched deterministically from a pinned Department for Transport Source Export to a road section, retaining count-point identity, observation year, counted or estimated status, direction, freshness, coverage and provenance. Missing minor-road observations remain unknown; compilation and an asset's eligibility do not depend on live DfT access.
_Avoid_: live API lookup, absent-means-zero, current traffic guarantee, route veto

**Traffic Challenge**:
A non-veto diagnostic emitted for an on-carriageway candidate when configured motor-traffic evidence is material. `traffic-high-on-carriageway-without-protected-space` is emitted only when protected-space absence is explicitly evidenced; unknown or conflicting protected-space evidence has its own diagnostic and is never treated as absence.
_Avoid_: safety verdict, traffic score, missing-space assumption, automatic exclusion

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
A schema-valid audit record for a compilation decision, stating its governing Criterion Status, effective Agent Review Policy, whether review was required, the complete fingerprinted menu, selected compiler action, small bounded set of decisive considerations, governed citations, responder mode, validation result, fallback trigger where applicable and affected feature identifiers. Agent-selected and fallback-selected parallel alignments use the same inspectable record shape, and the record never directly changes compiled state.
_Avoid_: free-form answer, silent edit, unrecorded deterministic skip

**Alignment Decision Explanation**:
A deterministic, replaceable presentation derived from one validated Agent Decision Record, its selected compiler-authored choice, decisive consideration findings and governed citations. It identifies whether selection was agent-made, deterministic near-equivalence or deterministic runtime fallback and may explain why alternatives remain rejected evidence, but it cannot add reasons, authority or evidence absent from the record.
_Avoid_: Agent Decision Explanation, agent-authored public rationale, approval note, hidden prompt summary

**Agent Review Policy**:
The exact set of Green, Amber, Red and Grey Criterion Statuses in Council Configuration that require an Agent Decision Request. It applies to the status governing an individual decision, never a Criteria Section aggregate; an empty set means no Agent Runtime is constructed or called.
_Avoid_: always-on agent, worst-section rollup, open-ended escalation

**Agent Decision Request**:
A stable, dependency-fingerprinted and schema-valid decision menu that names one exact criterion and question, its governed evidence and deterministic findings, and a finite ordered set of compiler-actionable choices. Returning it ends the current compilation invocation without publishing or retaining continuation state.
_Avoid_: blocked message, open-ended prompt, live continuation, heartbeat

**Parallel Alignment Agent Decision Request**:
A complete Agent Decision Request for one Parallel Alignment Candidate Set whose only actions select an eligible compiler-authored end-to-end alignment and whose high-level aggregated multi-dimensional findings, material flags, limitations, fallback hierarchy and citation pointers are all available within the current compilation. Raw area-wide datasets are excluded, missing facts remain explicit evidence gaps rather than agent retrieval tasks, and terminate, defer or investigate actions are never offered; a validated Agent Runtime choice or the Deterministic Alignment Fallback resolves the request in that invocation without an interactive pause.
_Avoid_: open-ended decision, officer prompt, suspended compilation, raw dataset dump, agent fact-finding, partial decision evidence, agent-authored route

**Agent Decision Choice**:
One compiler-authored item in an Agent Decision Request, identified by a simple stable identifier and declaring its concise meaning, predefined compiler action, expected consequence and mandatory constraints. `terminate` has the reserved meaning of stopping the run, preserving the previous valid publication and requiring a fresh compilation.
_Avoid_: free-form answer, agent-supplied parameter, validation waiver

**Decisive Alignment Consideration**:
One compiler-authored identifier for a governed evidence consideration already offered in a parallel-alignment decision request and selected by the responder as materially bearing on its choice. A small bounded set demonstrates which dimensions drove the choice without adding evidence, weights, geometry or free-form rationale; every identifier and its relationship to the selected option must validate together or the whole response is rejected in favour of the Deterministic Alignment Fallback.
_Avoid_: agent essay, invented evidence, unsupported reason, partial response acceptance, hidden weight

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
The optional provider-neutral seam through which compilation submits one complete fingerprinted Agent Decision Request and accepts its request identifier plus one offered choice identifier; a parallel-alignment response also returns a small bounded set of offered Decisive Alignment Considerations and can never introduce evidence. Calls are lazy, single-attempt, request- and token-limited, and protected by a configurable hard wall-clock deadline; a failed, unsupported or partially invalid parallel-alignment response is rejected as a whole and invokes the Deterministic Alignment Fallback rather than pausing compilation. A concrete model provider is an Adapter at this seam; Codex is not required.
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

**Visual Survey Request**:
A versioned, explicitly enabled request that binds finite compiler-authored evidence questions to exact governed feature IDs and location or corridor geometry fingerprints. Only an allow-listed provider adapter may answer it; the clean Baseline Scenario Compilation does not commission one.
_Avoid_: autonomous browsing, general area search, implicit provider access

**Desktop Imagery Observation**:
An attributable, source- and licence-bound observation from approved street-level, aerial/satellite or manually supplied imagery, retaining image date, retrieval date, viewpoint, coverage, limitations, confidence, privacy treatment and redistribution permission. Missing, old, obscured or conflicting imagery remains explicit, and the observation cannot create executable geometry or claim safety, legality, feasibility or design compliance.
_Avoid_: verified site survey, current-condition proof, route edit

**Officer-Accepted Desktop Observation**:
A Desktop Imagery Observation that an attributable human has accepted for use as bounded evidence in one Officer-Informed Scenario Compilation. The acceptance and observation are separately fingerprinted, and changed imagery or acceptance creates new scenario evidence identity.
_Avoid_: agent approval, silent evidence promotion, physical survey

**Physical Site Survey**:
An accountable in-person assessment governed outside the desktop imagery workflow. A visual-survey provider may request one as missing evidence but can never label its own output as one.
_Avoid_: Street View survey, aerial survey, agent observation

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
