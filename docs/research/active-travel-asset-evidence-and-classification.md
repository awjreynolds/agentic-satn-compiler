# Active-travel asset evidence and classification

Research for [Research authoritative active-travel asset evidence and classifications](https://github.com/awjreynolds/agentic-satn-compiler/issues/282).

- Retrieved: 2026-08-02
- Research branch: `codex/wayfinder-research-assets`
- Scope: source authority, classification and provenance contracts; no production implementation

## Decision in brief

Adopt a **claim-specific, configurable evidence hierarchy**, not one global ranking of datasets:

1. use the publisher that is authoritative for the claim being made;
2. use maintained mapped/open data as provisional evidence where that claim is absent;
3. use governed Local Connector Evidence to record attributable local observations and bridge otherwise missing evidence; and
4. retain every conflicting, missing or stale observation and create an Evidence Request without aborting compilation.

For example, the Walk Wheel Cycle Trust dataset is authoritative for whether it currently classifies a segment as NCN or as a Reclassified Route; the local surveying authority's Definitive Map and Statement is authoritative for recorded PROW class; a confirmed cycle-track order or legal highway record is authoritative for cycling rights; and an asset or as-built record evidences physical provision. None of those authorities automatically proves current width, surface, accessibility, ownership, engineering suitability, cost or deliverability.

Keep **Alignment Basis**, **Intervention State** and **Constraint Assessments** independent:

- Alignment Basis says what corridor or asset evidence a section follows.
- Intervention State says whether the selected SATN section is existing provision, requires upgrade, is a proposed new link, or is an unresolved gap.
- Constraint Assessments record legal/highway rights, land, condition, environment, utilities, traffic, maintenance, cost and other known/unknown constraints.

An asset discovered by any configured evidence source must not silently disappear. Incomplete physical or rights evidence prevents the compiler from asserting existing provision; it does not remove the asset. A continuous but unverified asset remains visible as an upgrade opportunity with explicit unknown constraints. A missing physical connection remains an unresolved gap. Compilation always produces a result.

The source hierarchy resolves evidence observations, not route policy. It therefore does not make an existing asset an unconditional route winner and does not replace the bounded Network Selection Profile in [ADR 0006](../adr/0006-preferred-strategic-alignments-support-the-lcwip-pipeline.md).

## Primary-source findings by asset class

### Current NCN and connector links

Use the Walk Wheel Cycle Trust's [National Cycle Network (Public) item](https://www.arcgis.com/sharing/rest/content/items/5defd254e78745bfb12d0456abc1bcf1?f=json) as the authoritative source for the custodian's current route classification. Its metadata describes UK coverage, weekly updates, an Open Government Licence v3.0, and known error risk. The layer distinguishes NCN, regional routes, signed connector links and promoted routes; it also carries traffic-free/on-road, Greenway, open-status, surface-quality, lighting and road-class observations. The [feature-layer schema](https://services5.arcgis.com/1ZHcUS1lwPTg4ms0/arcgis/rest/services/National_Cycle_Network_Public/FeatureServer/0?f=pjson) documents those fields and exposes `GlobalID` and `SegmentID` separately from the mutable service row ID.

Govern the downloaded bytes as a Source Export. Do not compile directly against an unpinned live response. Record the layer edit time, retrieval time, exact query, returned feature count and content hash. Preserve the publisher's Ordnance Survey and OpenStreetMap attribution stated in the item metadata as well as the OGL attribution.

Interpretation limits:

- `RouteType=NCN` establishes the custodian's current NCN classification, not a legal right or an adequate cycle facility.
- `RouteType=LINK` is a signed connector to an NCN/RCN, not part of the numbered route; retain it as its own Alignment Basis rather than hiding it.
- `Desc_=TrafficFree` says the route is closed to public motor vehicles and may be a footway, cycle path or bridleway. It does not identify the underlying statutory right by itself.
- `OpenStatus`, `Surface`, `Quality` and `Lighting` are useful source-release observations, but the public schema does not provide a feature-level observation date. They must not be generalized into a guarantee of present condition.

The Trust says route maintenance may belong to the relevant landowner; NCN identity must therefore remain distinct from ownership and maintenance responsibility ([NCN navigation FAQ](https://www.walkwheelcycletrust.org.uk/about-us/frequently-asked-questions-faqs/faqs-navigating-the-network/)).

### Reclassified NCN

Prefer a governed export of the custodian's Reclassified Routes data for Reclassified NCN identity, but treat its current data contract as unresolved: the previously identified [Reclassified Routes (Public) item](https://www.arcgis.com/sharing/rest/content/items/fbb7b0ceeb30470c973596ee4b7a58b9?f=json) returned an internal error when checked on 2026-08-02, so its availability, update cadence and licence could not be verified. Do not ingest or publish it until an accessible export with verifiable metadata is obtained. The custodian's reclassification announcement remains primary evidence for the classification semantics: these routes were formerly NCN and are intended for more experienced users, while removed sections are a separate category.

The 2020 change also removed some busy on-road sections from public mapping altogether. The Trust's [reclassification announcement](https://www.walkwheelcycletrust.org.uk/our-blog/news/walk-wheel-cycle-trust-to-enhance-family-offer-on-the-national-cycle-network-as-uk-moves-out-of-lockdown/) distinguishes 3,090 miles reclassified for experienced users from 753 miles removed because they fell too far below its quality aspiration. Consequently:

- use **Reclassified NCN** rather than “deprecated NCN” as the Alignment Basis;
- do not infer that a reclassified segment has ceased physically or legally to exist;
- do not grant `existing provision` merely because it was once NCN; and
- preserve previously governed current-NCN observations as historical evidence rather than rewriting them.

### Greenways, cycle tracks and shared-use paths

“Greenway” is descriptive, not a statutory access class. The NCN schema describes it as a linear path, often along a natural corridor, and applies the field only to traffic-free sections. A Greenway record is therefore strong corridor evidence but needs separate rights and condition observations.

A cycle track has a legal meaning: a highway over which the public has a right of way by pedal cycle, with or without a right on foot (excluding pedal cycles that are motor vehicles within the statutory definition). That definition is set out in the [Cycle Tracks Act 1984](https://www.legislation.gov.uk/ukpga/1984/38) and summarized in [Cycle Infrastructure Design, LTN 1/20](https://www.gov.uk/government/publications/cycle-infrastructure-design-ltn-120). An authority can also convert a footpath into a cycle track through the statutory order process ([DfT national transport casework](https://www.gov.uk/government/groups/national-transport-casework-team)).

Prefer, in order appropriate to the claim:

1. a council cycle-track order, dedication/adoption record or highway record for cycling rights;
2. a dated as-built/completion record or authoritative highway asset inventory for physical provision;
3. the NCN/Greenway custodian record for its route classification;
4. maintained Ordnance Survey or OSM mapping as provisional physical evidence; then
5. governed Local Connector Evidence.

Treat “shared-use path” as a physical/use description until it is backed by an authoritative rights record. A council project map or LCWIP can show intention and geometry, but a proposed or consulted scheme is not existing provision. Even a completed asset record says nothing about present design quality unless a dated audit supplies it.

Where licensed and configured, [OS Transport Network](https://www.ordnancesurvey.co.uk/products/os-transport-network) is a useful monthly-updated source for physical road/path connectivity, path function and surface, and cycle-lane presence. It is not the council's Definitive Map and must not be used as the legal-rights authority.

### Public rights of way

For England, the highway authority's **Definitive Map and Statement** is the authoritative record of recorded public rights of way. Natural England's [local-authority responsibilities](https://www.gov.uk/guidance/public-rights-of-way-local-authority-responsibilities) call it the legal record and require authorities to keep the network recorded and open. The [B&NES Definitive Map page](https://www.bathnes.gov.uk/definitive-map-and-statement) confirms that its statement can also record width and limitations, but reveals material coverage/currency caveats: the original relevant date is 1956 and the formerly excluded City of Bath is being added progressively.

Apply the legal classes without converting them into condition findings:

| Alignment Basis | Public cycling right evidenced by recorded class | Initial interpretation for SATN evidence |
| --- | --- | --- |
| Public footpath | No; public right is on foot | Upgrade opportunity. Cycling rights/conversion and physical suitability are separate unknowns. |
| Public bridleway | Yes, subject to applicable restrictions and giving way | Reusable corridor evidence; condition, barriers, width and inclusive suitability remain unknown. |
| Restricted byway | Yes, for non-motor transport | Reusable corridor evidence; condition and restrictions remain separate. |
| Byway open to all traffic | Yes, alongside motor access | Reusable corridor evidence, but not traffic-free and not automatically low traffic. |
| PROW, class unresolved | Unknown | Visible opportunity with an Evidence Request; never assume the most permissive class. |

This covers the statutory PROW classes relevant to the compiler. Any nonstandard, incomplete or otherwise unresolved PROW classification uses `prow-class-unknown`; permissive access is stored as a separate revocable-access claim rather than being invented as another statutory PROW class.

The class/use summary is supported by [GOV.UK's public-rights-of-way guide](https://www.gov.uk/right-of-way-open-access-land/use-public-rights-of-way) and [Active Travel England's PROW guidance](https://activetravelengland.gov.uk/planning-active-places/public-rights-way). LTN 1/20 also states that a footpath carries a right on foot only and that PROWs are shown on the authority's Definitive Map.

Absence from a digital extract is not conclusive absence. It may mean the extract is unavailable, a legal event has not reached it, the City of Bath coverage is incomplete, or an unrecorded/higher right is unresolved. Retain those states distinctly. Do not substitute an OS leisure map or OSM designation for the Definitive Map when making a legal-rights claim.

### Former railways

There is no complete official national source in the reviewed material that proves former-railway continuity, current ownership and public access together. National Highways' [Historical Railways Estate](https://nationalhighways.co.uk/our-work/historical-railways-estate/about-the-hre/) covers roughly 3,100 structures and some associated land, not every former line. It also notes that many corridors disappeared or were sold privately. Presence in that estate is authoritative for the specified managed asset, not for an available end-to-end corridor.

Use official current owner/estate records, council land/highway records, completed scheme records and title evidence for their exact claims. Historic OS/railway plans, OS topographic evidence and `railway=abandoned` OSM geometry can identify a former alignment, but not current public access, ownership, continuity or suitability. The OSM [`railway=abandoned` convention](https://wiki.openstreetmap.org/wiki/Tag%3Arailway%3Dabandoned) means rails have been removed but former-railway evidence remains visible; it explicitly does not describe a usable path.

A former railway should normally enter as an upgrade opportunity or proposed-new-link basis. It becomes existing provision only where separate current cycling-access and continuity evidence supports that state.

### OpenStreetMap fallback

OSM is a valuable maintained fallback for geometry and contributor-observed attributes, licensed under the [Open Database Licence](https://www.openstreetmap.org/copyright). Govern an immutable extract with its extraction time, replication sequence/timestamp where available, query or polygon, ODbL attribution and content hash.

Use tags as provisional assertions, not legal proof:

- `highway=cycleway` conventionally denotes a route designated for bicycles, while access, surface, smoothness, width and segregation require additional tags ([OSM cycleway tagging](https://wiki.openstreetmap.org/wiki/Tag%3Ahighway%3Dcycleway)).
- `highway=path` is deliberately generic; interpret it only with mode-specific access and `designation=*` tags.
- `designation=public_footpath`, `public_bridleway`, `restricted_byway` and similar values are contributor assertions intended to mirror legal classes. The OSM UK guidance itself says to consult the relevant local authority for actual legal rights ([OSM UK access provisions](https://wiki.openstreetmap.org/wiki/Access_provisions_in_the_United_Kingdom)).
- `bicycle=permissive` is revocable permission, not a public right.
- a `route=bicycle` relation with `network=ncn` represents a mapped route relation, but the Walk Wheel Cycle Trust layer remains the classification authority ([OSM route relation](https://wiki.openstreetmap.org/wiki/Relation%3Aroute)).
- `railway=disused` or `railway=abandoned` identifies railway lifecycle evidence, not public access.

Conflicting OSM tags should not be normalized into one optimistic value. Preserve the raw tags used, the applicable country-specific default-access rules, and a parser-contract version.

### Governed Local Connector Evidence

Local knowledge is legitimate planning evidence: DfT's [LCWIP technical guidance and tools](https://www.gov.uk/government/publications/local-cycling-and-walking-infrastructure-plans-technical-guidance-and-tools) says local knowledge is a crucial input and stakeholder views should be sought. It still needs a bounded evidence contract.

A Local Connector Evidence record may establish an attributable observation such as “a bridge/path was present and connected these mapped points on the observation date.” It may cite an official order, owner statement or survey that separately establishes rights or condition. An officer assertion alone must not create legal cycling rights, ownership, structural adequacy, public availability or a precise cost.

Require:

- a stable record ID, version and supersession lineage;
- geometry or exact endpoint references, with CRS and geometry fingerprint;
- author identity in the controlled record, accountable public role, organisation and recorded time;
- observation date, method (`site`, `desktop`, `document`, `stakeholder`) and evidence attachments/citations with hashes;
- each atomic claim (`physical_connection`, `public_access`, `cycling_access`, `surface`, `barrier`, `completion_status`, and so on), its evidence mode (`observed`, `documented`, `inferred`, `unknown`) and limitations;
- verification status (`unverified`, `corroborated`, `authority_verified`, `superseded`), verifier role and verification date;
- topology endpoints and whether bidirectional continuity was actually established; and
- licence/access/publication controls, Explicit Unknowns and generated Evidence Requests.

An unverified connector can participate in candidate generation as a provisional section. It cannot silently close a topological break. Without continuous evidence-backed geometry, the output remains an unresolved gap.

## Evidence resolution contract

### Resolve per claim, not per feature

One physical section may legitimately have several Alignment Bases and several sources: for example, a current NCN on a Greenway over a former railway and recorded bridleway. Store the complete set of bases and evidence observations. A configured `primary_alignment_basis` may drive the map halo, but it must not delete the other identities.

Recommended source-authority roles are:

- `custodian_classification` — current/reclassified NCN and Greenway classification;
- `legal_highway_record` — PROW class, cycle-track status and legal events;
- `asset_owner_record` — asset identity/ownership for the exact property or structure;
- `scheme_delivery_record` — proposed, under-construction or completed scheme state;
- `authoritative_topography` — physical path/road/cycle-lane mapping, not rights;
- `community_mapped_observation` — OSM physical and tagging observations; and
- `governed_local_observation` — Local Connector Evidence.

The Network Selection Profile should configure accepted source families and precedence for each claim type. A lower-ranked source fills a missing claim provisionally; it does not rewrite a higher-ranked observation. A newer physical observation may challenge an older asset record, but it cannot override a legal record merely by being newer.

### Conflict, stale and missing semantics

Use closed states on each required claim:

- `supported` — one effective observation, with no material contradiction;
- `provisional` — only a configured fallback source supports it;
- `conflicting` — accepted observations materially disagree;
- `stale` — the source-specific freshness rule has expired;
- `missing` — coverage exists but the required claim is absent;
- `coverage_unknown` — source availability or spatial coverage is unresolved; and
- `not_applicable` — the claim does not apply to this asset.

Freshness limits belong to configured source families, because weekly NCN data, monthly OS data, legal orders and site observations have different lifecycles. Never treat `missing`, `stale` or `conflicting` as false, safe, unusable, or zero.

Resolution behaviour:

1. retain the asset and every source observation;
2. select an effective observation only where the claim-specific hierarchy permits it;
3. record the conflict or fallback reason and all rejected observations;
4. create a typed Evidence Request naming the claim, geometry, preferred source and smallest verification required;
5. retain any continuous evidence-backed section for hybrid candidate assembly;
6. expose a missing connection as an unresolved gap rather than drawing an inferred join; and
7. finish compilation through the declared deterministic selection fallback or gap semantics.

Do not merge mismatched legal and observed geometries into an unattributed hybrid. Retain a legal-line observation and an observed-used-line observation separately, record their spatial relationship, and request reconciliation.

## Classification contract

### Alignment Basis

Use a closed, extensible vocabulary at section level:

```text
current-ncn | ncn-link | greenway | cycle-track | shared-use-path
reclassified-ncn | public-bridleway | restricted-byway | public-footpath
byway-open-to-all-traffic | prow-class-unknown | former-railway
local-connector | a-road | b-road | classified-unnumbered-road
unclassified-road | proposed-new-corridor
```

Store `alignment_bases[]` plus a configured `primary_alignment_basis`. “Existing asset” is not an Alignment Basis because it conflates identity with Intervention State.

### Intervention State

Use exactly:

- `existing-provision` — current continuous cycling provision is positively evidenced; this does not certify design quality;
- `upgrade-required` — a reusable continuous corridor exists, but an intervention or unresolved rights/condition constraint prevents it being represented as current provision;
- `proposed-new-link` — governed candidate geometry exists but requires creation rather than upgrade of a reusable section; and
- `unresolved-gap` — continuous evidence-backed geometry does not exist.

Derive state from claims, not from one asset label. Default examples:

- public footpath -> `upgrade-required` unless independent evidence establishes cycling provision;
- bridleway/restricted byway -> `existing-provision` only when the intended cycling connection and continuity are established; otherwise `upgrade-required` with condition/access restrictions explicit;
- current NCN/cycle track/Greenway -> `existing-provision` only with current cycling-access and continuity evidence; a dated deficiency can make it `upgrade-required`;
- Reclassified NCN -> no automatic state; assess its current physical/legal evidence;
- former railway -> `upgrade-required` or `proposed-new-link` unless separately evidenced as current cycling provision; and
- missing bridge/link geometry -> `unresolved-gap`, never a straight-line proposal.

### Constraint Assessments

Keep at least these independent and four-state (`known-clear`, `known-constraint`, `unknown`, `not-applicable`):

- lawful cycling access and temporary/permissive restrictions;
- land/highway rights and ownership;
- surface, width, barriers, gradients and current condition;
- continuity, crossings and structural assets;
- traffic exposure;
- environment/heritage;
- utilities and drainage;
- maintenance responsibility;
- scheme/delivery status; and
- cost/feasibility.

No source classification alone may populate `known-clear` for condition, land, cost or feasibility.

## Minimum provenance for an adapter contract

Every normalized source observation should retain:

```yaml
observation_id: stable compiler identity
claim_type: asset_identity | route_class | legal_access | geometry | condition | ownership | scheme_status | ...
value: typed value
evidence_mode: observed | documented | modelled | inferred | unknown
source:
  family: configured source family
  publisher: legal/custodian publisher
  dataset: exact dataset and layer
  source_authority_role: one of the configured claim roles
  source_feature_key: publisher key, if guaranteed stable
  source_feature_key_scope: export | cross_release
  release_or_effective_date: date or explicit unknown
  retrieved_at: timestamp
  source_update_time: timestamp or explicit unknown
  expected_update_frequency: duration or event_driven
  licence: identifier and required attribution
  source_uri: retrieval/document URI
  raw_sha256: received Source Export hash
  source_export_fingerprint: ADR-0011 identity
geometry:
  source_crs: declared CRS
  normalized_crs: EPSG:27700 for the current GB contract
  geometry_fingerprint: canonical geometry identity
coverage:
  spatial_extent_or_partitions: explicit coverage
  availability: available | no-data | explicit-unknown
ingestion:
  contract_id: versioned adapter/parser
  contract_fingerprint: implementation/schema dependency
verification:
  status: unverified | corroborated | authority-verified | superseded
  verifier_role: optional controlled role
  verified_at: optional timestamp
quality:
  freshness_state: current | stale | unknown
  limitations: closed identifiers plus bounded text
  conflict_ids: other observation IDs
lineage:
  input_observation_ids: derived evidence inputs
```

This extends rather than weakens [ADR 0011](../adr/0011-stable-evidence-partition-and-dependency-identities.md): raw Source Exports remain authoritative, mutable service/database IDs are not portable identities, original CRS/transform are explicit, and derived observations bind exact source attestations and ingestion contracts.

## Implementation-ready recommendation

1. Add one source-adapter contract that emits atomic evidence observations, never a pre-collapsed “best asset” row.
2. Configure source families and precedence per `claim_type`; ship no council-specific source URL or freshness threshold in compiler logic.
3. Implement initial adapters for current NCN, Reclassified NCN, council Definitive Map/Statement exports, council cycle/highway asset records, OSM extracts and Local Connector Evidence. Treat OS Transport Network and official former-railway/estate sources as optional licensed adapters.
4. Normalize every discovered section to the complete Alignment Basis set, Intervention State, Constraint Assessments, evidence disposition and provenance roster.
5. Preserve partial reusable sections and assemble hybrids only at proven Alignment Choice Points; missing links remain gaps.
6. Publish the selected SATN independently from optional Existing and Upgradeable Assets and Non-Selected Parallel Alignment layers, but retain every asset's selection/disposition reason.
7. Add proving scenarios for: current NCN versus A-road; hidden NCN connector; Reclassified NCN; footpath conversion opportunity; bridleway with unknown surface; conflicting DMS/OSM geometry; partial former railway plus local connector; missing bridge; stale official physical evidence; and absent official coverage with OSM fallback.

## Explicit unknowns and follow-up Evidence Requests

- Confirm the obtainable machine-readable B&NES Definitive Map/Statement export, its legal-event update process, stable identifiers, licence and public-publication rights. The public web page confirms authority but not a reusable open-data contract.
- Confirm the B&NES highway/cycle asset register and which legal orders, completion/as-built records and maintenance records can be exported with stable geometry.
- Confirm whether the NCN public service's `SegmentID` is guaranteed stable across releases; until then scope it to one Source Export and use canonical content identity across releases.
- Establish source-specific freshness policies with publishers; do not invent a universal age limit.
- Establish whether OS Transport Network licensing permits the intended governed cache and public derivative layers for each deployment.
- Identify official owner/title and structure evidence needed before any former-railway section can receive land or structural `known-clear` findings.
- Define the governed acceptance and authority-verification workflow that may promote Local Connector Evidence without exposing personal source material publicly.
