# SATN method reset

B&NES implementation and real-data verification, 6 September 2026. The owner authorised execution through a real B&NES proof. The policy below is the intended POC; the implementation evidence distinguishes what the local build proves from wider regional work. The [Wayfinder map](https://github.com/awjreynolds/agentic-satn-compiler/issues/421) indexes the authoritative decisions.

## Agreed selection policy

Apply [case-by-case route selection](https://github.com/awjreynolds/agentic-satn-compiler/issues/424) to useful interurban connections. The Main network is a legible set of continuous journeys, not all classified roads, every asset, a village-access tree or a graph made connected at any cost. The amended policy in ADR 0026 must replace its earlier universal A-road retention interpretation during implementation.

A-road corridors, current/former NCN routes, existing cycleways, Greenways and suitable bridleways must enter consideration naturally. Reuse and pragmatic deliverability are strong preferences; explain departures. B-roads require a genuine missing-interurban-link justification. Village-only branches remain Access Support. Shared sections appear once; complementary routes need distinct strategic purpose rather than proximity alone.

Choosing a cycleway does not remove wider A-road improvement needs. Choosing an A-road corridor does not assert that cycling in its present carriageway is suitable. Existing asset identity survives either selection outcome.

## Recommended compilation sequence

1. Establish the urban connections and cross-boundary journeys being served from settlement and adopted-plan evidence. Do not generate Main from every nearest-neighbour village pair.
2. Assemble coherent corridor options using the preferred source evidence. Preserve recognisable assets such as the Bristol–Bath Railway Path rather than stitching unrelated fragments merely because their local scores look attractive.
3. Compare complete options for the same journey. Explain the preferred route and material trade-offs without hidden weights. Preserve genuine complementary connections and reuse shared sections.
4. Check that the selected journey really follows its stated geometry through crossings and transitions. A close line or a low component count is insufficient. Show an unresolved connection honestly; do not draw a synthetic connector as existing provision.
5. Attach village-access branches separately.
6. Identify improvements along the preferred network and prioritise them separately. Retain known wider A-road improvement needs separately from Main membership; this does not require a new exhaustive road inventory. An existing useful path stays in Main even if it requires no new investment.

These are implementation recommendations, not a new graph framework. [The method research](https://github.com/awjreynolds/agentic-satn-compiler/issues/423) distinguishes our physical-network-then-improvement approach from Oxfordshire's prioritisation of abstract desire lines before physical optioneering.

## Map interaction recommendation

The owner has confirmed solid preferred routes and dashed potential alternatives. Dashed means a choice was made, not unbuilt, unsafe, rejected forever or a network gap. Choice attribution identifies compiler recommendation versus officer decision; it does not imply council adoption.

Keep Main and useful place labels visible by default. Reveal the related alternative when a route is selected; provide an Alternatives toggle for the wider comparison. Access Support and the complete asset inventory remain separate optional views. Do not draw every raw candidate as a strategic alternative.

Hover gives the route name or places connected, preferred/alternative role, and a short selection reason. Click or keyboard focus opens a persistent comparison showing:

- the preferred route and any considered potential alternatives;
- who chose it and the plain-language reason;
- existing provision, known upgrades and proposed sections;
- material unknowns that could change the choice.

Touch users need the same information through selection. Layer toggles and hover must not recompute route selection. When several route options share geometry, identify the relevant journey in the comparison rather than implying that a shared section was discarded everywhere.

Show infrastructure state separately from selection status, using labelled details and an optional state view. A route may contain existing, upgrade and proposed sections. Do not call the whole route built merely because some sections exist. Missing evidence is an unknown, not zero quality or a survey-based POC completion gate. Do not use the dashed-alternative style for gaps.

## Evidence of usefulness

Use the B&NES reference journeys below to inspect the result geographically, not merely to prove graph connectivity. For each journey, trace the preferred corridor, inspect source assets and transitions, explain any B-road exception, and open the comparison where genuine alternatives were considered; otherwise state that none was retained. A useful existing route must either participate or have a visible reason why it does not serve the chosen connection.

The reference set is source-informed and provisional; it does not silently resolve ambiguous dictated waypoints or approve physical alignments. The ATM is a comparison reference, not a requirement to copy every line.

| Reference journey | Evidence to exercise | What the comparison must establish |
| --- | --- | --- |
| Bath–Bristol | Existing Bristol–Bath Railway Path/NCN 4 and the ATM A431 connection | The railway path enters naturally, shared geometry appears once, and any preferred alternative has an explained journey-level reason. |
| Bath–Peasedown St John–Radstock | ATM A367 sections and the Bath Old Road quiet alternative | A coherent through-journey, with the alternative separately inspectable rather than unrelated fragments promoted to Main. |
| Radstock–South Bristol | ATM relationships through Midsomer Norton/Farrington Gurney and Whitchurch, including A362/A37 options | The individual relationships form a useful continuous journey; do not claim the ATM supplies a single adopted Radstock–Bristol alignment. |
| Bath–Keynsham and Keynsham–East Bristol | ATM links using existing PRoW, NCN 4 and A431 options | Connections join the shared network honestly; PRoW designation alone does not establish cycling suitability. |

Source: [B&NES ATM Part 6, existing-network discussion and Table 8.1, printed pp. 92–95 and 112–116](https://www.bathnes.gov.uk/sites/default/files/Active%20Travel%20Masterplan%20-%20Final-part-6.pdf), as examined in the [network-method research](https://github.com/awjreynolds/agentic-satn-compiler/blob/research/satn-reset-network-method/docs/research/satn-reset-network-method.md). These observations are proposed acceptance checks, not claims that a current build passes.

The dictated “Temple Comb” waypoint remains unconfirmed. Do not silently substitute Temple Cloud or Templecombe. The Radstock–South Bristol example above can test the method without claiming to fulfil that exact waypoint request.

## Agreed delivery priority

[Strategic importance leads the programme](https://github.com/awjreynolds/agentic-satn-compiler/issues/425), with practical deliverability shown separately. Officers can identify achievable near-term opportunities without silently demoting difficult connections the network needs. Network membership is unchanged by investment priority.

Use available evidence and explain uncertainty. This decision does not authorise numerical weights, invented costs or demand, automatic funding decisions, or replacing an explicitly configured council appraisal policy. Keep missing observations visible and do not fabricate an ordered programme when the required strategic evidence is absent.

## Scope of the handoff

No new runtime provider, security framework, audit infrastructure, scheme-design process or regional deployment is needed to prove this method. Implementation should change only what prevents the agreed journey and comparison behaviour, with focused checks against the reference journeys. These planning notes do not authorise claims that the current outputs meet those checks.

## Implementation evidence

The first implementation increment removes automatic promotion of supplied urban B-road branches into Main, while retaining routable B-road evidence for necessary continuity connections. An unreachable component no longer prevents connections among other reachable Main components.

A selected alternative can displace an injected urban A-road section when an admitted A-road candidate has the exact same routing edges and endpoints, and the selected candidate serves the same connection and role. Independent A-road sections remain protected. This closes a demonstrated duplication case; it does not establish that every real-world overlapping corridor is recognised as the same journey.

Selection records now distinguish the actual choice explanation and attribution from candidate admission. Ordinary compilation records its existing comparator's reason directly; explicit external preferences without a rationale remain marked as such. Selecting a route opens its same-journey comparison and reveals admitted alternatives as dashed linework without enabling the wider alternatives layer. Closing the comparison clears that focused linework.

Focused compiler, publication and browser tests exercise these behaviours. The browser fixture uses synthetic Bath–Saltford geometry through the production compilation and publication path; it is not geographic validation of the reference journeys above. The real-data checks below supersede the synthetic fixture for the in-area journeys. Existing regional deployments do not contain this increment.

### Real B&NES recovery evidence

The cached identity-recovery build completed in 273.68 seconds. Shared routing/effective-graph source identities eliminated all 135 missing-edge failures and populated all 236 candidate sets. This repaired data handoff, but exposed a separate methodological error: the former interurban preparation paired access communities rather than the actual cities and towns. Its NCN 24 comparison was Southstoke–Midford, not Bath–Radstock.

Urban journey preparation now uses in-area city/town points and observed adjacency in the physical graph. It creates no Cartesian place-pair list or village Main obligations. The journey obligation survives candidate changes and mesh simplification; interurban journeys retain rural scope. Legacy route attribution uses actual edge extent and the configured source precedence, preserving minority bases rather than treating any NCN crossing as a wholly existing cycling route. The vNext appraisal policy is unchanged.

The source-recovery build completed in 368.36 seconds, with local outputs under `build/banes-source-recovery`. Bath–Keynsham produced a selected 11.60 km cycleway option and two admitted alternatives, including an A-road option. The real browser comparison showed readable endpoints, choice reasons and current/former NCN evidence. Its primary mapped-cycleway label reflects 6,211 m of mapped cycleway versus 6,146 m of greenway evidence; no NCN evidence was lost at graph handoff.

The source-recovery build was **not acceptance**: three Radstock journey candidate sets were empty, and retained A-road inventory made the preferred/alternative distinction ambiguous. Required urban journeys now appear as gaps when unroutable rather than being filtered as optional attempts.

The subsequent journey-recovery build (`run-c2fda88d20c3`, `build/banes-journey-recovery`) completed in **175.15 seconds**. Its legacy urban-journey mode binds city/town endpoints and routes on the same base physical graph. It stops injecting the separate A-road inventory into Main and stops generating obsolete access-community/official-chain route comparisons in that mode. A-road candidates and source context remain available. vNext and legacy inputs without preferred urban adjacency retain their existing path.

All five in-area urban journeys have preferred routes and admitted alternatives: Bath–Radstock, Bath–Keynsham, Radstock–Midsomer Norton, Radstock–Keynsham and Keynsham–Midsomer Norton. The strategic projection has five selected journeys, 14 alternatives and no strategic journey gaps, compared with 847 selected fragments in the previous build. This is not a claim that all access or school obligations are resolved: the overall run remains reviewable.

Bath–Radstock now compares a 19.91 km mapped-cycleway-led route with a 14.65 km A-road option and other retained options. The configured reuse preference selects the cycling asset; the shorter A-road option remains an explicit alternative. Bath–Keynsham selects its 11.60 km reusable option. These are compiler recommendations, not an adopted alignment or proof that every metre is already suitable. The real browser check opens the Radstock–Bath comparison and renders all three related alternatives as dashed lines while the global Alternatives switch remains off.

The five semantic journey records preserve complete route geometry and comparisons. The map now draws their shared physical sections once, using exact coordinate-segment equality without rounding or a proximity threshold. The real Main layer has 2,983 segment appearances in its complete journey records and 1,272 unique physical segments in 11 display features. Seven display features serve multiple journeys. All 75 selected-route and alternative records, including Access Support, were exactly unchanged after republication.

A real browser click on a shared section offered Keynsham–Midsomer Norton, Radstock–Keynsham and Radstock–Bath. Selecting Radstock–Bath opened its three considered alternatives while leaving the global Alternatives switch off; closing cleared the focused comparison. The chooser stayed pinned while moving to its buttons. Focused publication/display tests passed (24), browser checks passed (2), and independent Sol review found no necessary correction. The retained semantic result was republished through the normal publication writer, including PDF generation and publication validation.

The geographic check found one connected endpoint graph over the four in-area towns/cities and satisfied route topology for the selected Bath–Radstock and Bath–Keynsham routes. The A-road alternative has retained A367 source rows for Wellsway, Dunkerton Hill and Roman Road. The selected routes retain provenance for Two Tunnels Greenway and the Bristol & Bath Railway Path respectively. NCN 4 context has exact geometric contacts with Bath–Keynsham; this is evidence of participation, not a claim that the route uses every metre of the named asset. The local proof files are `urban-journeys.json`, `display-verification.json` and `shared-browser-verification.json` under `build/banes-journey-recovery`.

The cached B&NES graph does not reach Bristol. Its nearest available node is about 5.55 km from the Bristol city point; this cannot prove a complete Bath–Bristol journey. B&NES can prove the railway-path portion within its coverage; a full Bristol endpoint requires the wider regional input.
