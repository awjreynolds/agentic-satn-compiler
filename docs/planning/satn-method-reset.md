# SATN method reset

Planning handoff, 6 September 2026. This describes the intended POC; it is not a claim that the compiler or published maps implement it. The [Wayfinder map](https://github.com/awjreynolds/agentic-satn-compiler/issues/421) indexes the authoritative decisions.

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

## Delivery priority remains a separate decision

For the POC, recommend retaining strategic benefit and practical deliverability as separate explanations. Do not introduce council approval workflows, invented cost estimates, numerical weights or automatic short/medium/long-term labels.

A programme ranking still needs a policy choice when a high-value difficult connection competes with an easier improvement delivering less network benefit. Route reuse preference does not by itself settle that investment choice. Record that choice before presenting an ordered programme as the preferred recommendation; an unranked improvement list can remain useful meanwhile.

## Scope of the handoff

No new runtime provider, security framework, audit infrastructure, scheme-design process or regional deployment is needed to prove this method. Implementation should change only what prevents the agreed journey and comparison behaviour, with focused checks against the reference journeys. These planning notes do not authorise claims that the current outputs meet those checks.
