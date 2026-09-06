# Research note: separate strategic network selection from delivery prioritisation

- Issue: [#423, “Separate strategic network selection from delivery prioritisation”](https://github.com/awjreynolds/agentic-satn-compiler/issues/423)
- Research date: 6 September 2026
- Scope: transfer the useful ordering and decision units from the B&NES Active Travel Masterplan and Oxfordshire SATN into the B&NES strategic network method.
- Boundary: research and method recommendation only. This note does not select B&NES routes or change compiler/runtime behaviour.

## Decision-ready finding

The two authorities support a two-layer method:

1. **Strategic network selection:** choose a coherent, continuous set of inter-urban connections at an abstract corridor/desire-line level, using existing assets and strategic evidence. This answers **which relationships belong in the preferred network**.
2. **Delivery prioritisation:** split selected connections into deliverable segments or interventions and decide **which improvements should be developed or funded first**. This is a later investment decision and must not create duplicate strategic redlines.

Oxfordshire does not build every physical route and then rank the resulting alignments. It builds and consults an abstract desire-line network, converts it to segments and sub-segments, prioritises those straight-line units, and only then develops potential on-the-ground alignments for the resulting priority network. B&NES's adopted masterplan describes the same separation in less formal terms: it presents a comprehensive future network, then says a later process will prioritise routes and subject selected routes to engineering assessment.

For this repository, “full network then prioritise” should therefore mean **complete the abstract strategic candidate/preferred network before ranking delivery interventions**. It should not mean “draw every possible physical alignment before deciding which strategic relationships matter.”

## Primary-source findings

### B&NES Active Travel Masterplan

The council's masterplan says the existing urban and rural network should be linked into “continuous and coherent routes.” It specifically identifies the Bristol–Bath Railway Path as part of NCN Route 4, alongside NCN 24, NCN 244, the Two Tunnels Circuit and the wider PRoW network. Existing cycleways, railway paths, greenways and rights of way are therefore first-class network evidence, not only future scheme opportunities. ([ATM document page](https://www.bathnes.gov.uk/document-and-policy-library/active-travel-masterplan); [ATM Part 6, pp. 92–95](https://www.bathnes.gov.uk/sites/default/files/Active%20Travel%20Masterplan%20-%20Final-part-6.pdf))

The cycle network has distinct route types. Strategic routes prioritise directness and efficiency between key origins and destinations, often along main transport corridors. B&NES says most strategic route mileage will be off-road, with limited road use where routes pass through villages and communities on those corridors. Quiet Routes are a separate safer/low-traffic layer, and Community Connections link strategic or quiet routes to smaller villages and rural areas. ([ATM Part 4, pp. 57–62](https://www.bathnes.gov.uk/sites/default/files/Active%20Travel%20Masterplan%20-%20Final-part-4.pdf))

The masterplan's growth-area method is also sequential: assess planned growth and access to services, evaluate existing facilities, identify missing links, and identify multi-modal hubs. Its cycling network uses WERTM origin–destination data to identify 18 high-demand pairs, then adds route proposals using local knowledge and stakeholder discussions. ([ATM Part 6, pp. 102–109](https://www.bathnes.gov.uk/sites/default/files/Active%20Travel%20Masterplan%20-%20Final-part-6.pdf))

The route table shows how existing and proposed infrastructure can be combined within one inter-urban relationship. Examples include the existing Bristol–Bath path plus an A431 strategic connection; an A362 strategic route with a PRoW/quiet alternative; and a B3115 strategic route with a quieter village alternative. The table also includes A37, A39, A367 and B3355 options. This is evidence that road-class corridors are candidate sources inside a wider route choice, not a rule that every route must follow a classified road. ([ATM Part 6, pp. 110–116, Table 8.1](https://www.bathnes.gov.uk/sites/default/files/Active%20Travel%20Masterplan%20-%20Final-part-6.pdf))

The delivery boundary is explicit. The masterplan says it is aspirational, will guide investment and funding bids, and will have a later Delivery Plan. Its next steps call for detailed route prioritisation using potential impact, feasibility, demand and connectivity against existing and planned transport networks; selected routes then receive engineering, environmental and cost assessment, with alternatives where a route proves infeasible. ([ATM Part 7, p. 123](https://www.bathnes.gov.uk/sites/default/files/Active%20Travel%20Masterplan%20-%20Final-part-7.pdf); [ATM Part 7, p. 128](https://www.bathnes.gov.uk/sites/default/files/Active%20Travel%20Masterplan%20-%20Final-part-7.pdf))

### Oxfordshire SATN

Oxfordshire's approved Stage 1 report describes four ordered components:

1. **Baseline analysis:** existing cycle routes and PRoW; LCWIPs, Greenways and allocated sites; demand sources; terrain, severance, population, deprivation, public transport and collisions.
2. **Network development:** an initial long list of straight desire lines, public engagement, a refined list, then longer **segments** and shorter **sub-segments**.
3. **Network prioritisation:** a SATN Index for settlements/locations, catchment aggregation around each segment, per-kilometre comparison, review against the council's existing pipeline, and Strategic/Primary versus Complementary/Secondary classification.
4. **Route optioneering:** translation of the prioritised straight lines into multiple potential physical alignments and typologies, with feasibility, design, landowner engagement and costing left for later work.

([OCC SATN Final Report, pp. 6–7](https://mycouncil.oxfordshire.gov.uk/documents/s70847/CMDIDS25042024%20-%20Annex%202%20-%20SATN%20Final%20Report.pdf); [OCC Stage 1 decision report, pp. 1–2](https://mycouncil.oxfordshire.gov.uk/documents/s70845/CMDIDS25042024%20-%20Strategic%20Active%20Travel%20Network%20Stage%201.pdf))

The decision unit changes during the process. The initial desire lines connect settlements and destinations. The refined network contains 46 longer segments. Each segment is then divided where it meets settlements, creating 176 sub-segments for more detailed scoring. Oxfordshire used both levels because sub-segments identify high-scoring sections while longer segments preserve longer-distance strategic continuity. ([OCC SATN Final Report, pp. 42–47, 50–54](https://mycouncil.oxfordshire.gov.uk/documents/s70847/CMDIDS25042024%20-%20Annex%202%20-%20SATN%20Final%20Report.pdf))

The report says the existing and emerging network was a key consideration and that, where practicable, SATN alignments should adopt or incorporate existing and proposed LCWIP, Greenways, PRoW and cycle-network routes. After scoring, the council compared high-scoring segments with its existing delivery pipeline. Strategic/Primary links include routes already being designed or developed through LCWIPs, developer-funded routes and other packages such as NCN upgrades. ([OCC SATN Final Report, pp. 35 and 50–56](https://mycouncil.oxfordshire.gov.uk/documents/s70847/CMDIDS25042024%20-%20Annex%202%20-%20SATN%20Final%20Report.pdf))

The Primary/Complementary split is a development focus, not a claim that secondary links have no value. Oxfordshire says every scored segment has strategic value; Complementary/Secondary links are recommended for development outside SATN and are not precluded from later design or delivery. The approved decision calls the first output a “prioritised straight desire line network” and approves a packaged Stage 2 for route/scheme development. ([OCC SATN Final Report, p. 55](https://mycouncil.oxfordshire.gov.uk/documents/s70847/CMDIDS25042024%20-%20Annex%202%20-%20SATN%20Final%20Report.pdf); [OCC SATN overview](https://letstalk.oxfordshire.gov.uk/strategic-active-travel-network-satn-yswd); [OCC Stage 1 decision](https://mycouncil.oxfordshire.gov.uk/ieDecisionDetails.aspx?ID=10387))

The council's consultation page separates three artifacts: the report, the straight desire-line map and a long list of potential on-the-ground alignments. It says the alignment map is an early optioneering exercise, is not a commitment to develop every route, and requires further feasibility and stakeholder/landowner engagement. ([OCC final-draft consultation](https://letstalk.oxfordshire.gov.uk/satn))

## Transfer recommendation for Issue #423

### 1. Keep one strategic selection layer

Represent the preferred strategic network as a canonical set of continuous inter-urban relationships between urban focal points or local-network gateways. A relationship may be assembled from several evidence segments, but it should have one authoritative preferred representation. Store optional physical alignments or alternatives against that relationship rather than drawing duplicate preferred redlines.

The candidate evidence review should require consideration of:

- A-road corridors and other main inter-urban corridors;
- current and former NCN alignments;
- existing cycleways, railway paths, greenways, bridleways and other usable PRoW;
- committed or already-designed schemes; and
- a B-road corridor only where it fills a genuine missing inter-urban connection.

The A-road rule is **mandatory consideration, not mandatory selection**. A-road evidence should be checked for every relevant urban relationship, then rejected where an existing traffic-free/PRoW/NCN route or a better continuous alignment serves the connection. B-roads should not become general urban spines merely because they are classified roads.

The Bristol–Bath Railway Path should enter this evidence review naturally as an existing NCN 4 strategic asset, rather than as a special exception or a newly generated redline. This follows the B&NES masterplan's treatment of the path.

### 2. Keep village access as a separate layer

Village connections should be represented as feeders or complementary links from a village to the strategic network or a larger service destination. A village that naturally lies on a selected inter-urban main corridor remains a valid through-node or corridor stop; “separate village access” means that a village-only branch does not justify adding another main corridor. This is a method recommendation inferred from the B&NES distinction between Strategic Routes and Community Connections, not a direct claim about the council's data model.

### 3. Add a separate delivery-priority layer

After the canonical strategic network is selected, create delivery records for route segments or concrete interventions. A delivery record can cover an existing asset needing an upgrade, a gap between existing assets, an access or crossing problem, or a new alignment option. It should carry the evidence and owner-defined priority result, while leaving the strategic network membership unchanged.

The implementation should not invent numerical weights. Oxfordshire's transferable lesson is the ordering and the use of two decision scales; its SATN Index and B&NES's later criteria are evidence for possible owner criteria, not weights to copy silently. If the council wants numerical prioritisation, the owner must choose the criteria, scales, treatment of missing evidence and tie-breaking rule.

The minimum method sequence is therefore:

1. inventory existing assets, committed schemes, urban focal points and cross-boundary gateways;
2. form the abstract inter-urban candidate relationships and review the required A-road/NCN/asset evidence;
3. select the coherent canonical strategic network, preserving continuity and one preferred redline per relationship;
4. split selected relationships into delivery segments and attach optional physical alignments/typologies;
5. prioritise delivery interventions using an explicitly owner-chosen, auditable method; and
6. maintain village feeders as a separate layer, with through-village sections retained where they lie on a selected inter-urban corridor.

This ordering follows Oxfordshire's desire-line-before-alignment process while retaining the B&NES masterplan's distinction between a coherent network and later route engineering/prioritisation.

## Remaining owner choices

The substantive unresolved choice is the strategic unit: should one canonical link be an urban-to-urban relationship assembled from segments, or a corridor segment between gateway nodes? Oxfordshire used both (46 segments and 176 sub-segments); the B&NES method needs one explicit identity rule to prevent duplicate redlines and to make delivery records attach consistently.

The related owner choices are:

- the authoritative list of urban focal points, LCWIP gateways and cross-boundary endpoints;
- what evidence qualifies as a former NCN alignment and which PRoW/bridleway legal or accessibility states are eligible;
- the test that proves a B-road is filling a genuine missing inter-urban connection;
- whether village feeders terminate at a strategic route, an urban gateway or a service destination; and
- the delivery-priority criteria, scales and priority classes.

## Sources

- [B&NES Active Travel Masterplan document page](https://www.bathnes.gov.uk/document-and-policy-library/active-travel-masterplan)
- [B&NES Active Travel Masterplan Part 4](https://www.bathnes.gov.uk/sites/default/files/Active%20Travel%20Masterplan%20-%20Final-part-4.pdf)
- [B&NES Active Travel Masterplan Part 6](https://www.bathnes.gov.uk/sites/default/files/Active%20Travel%20Masterplan%20-%20Final-part-6.pdf)
- [B&NES Active Travel Masterplan Part 7](https://www.bathnes.gov.uk/sites/default/files/Active%20Travel%20Masterplan%20-%20Final-part-7.pdf)
- [OCC SATN Final Report, March 2024](https://mycouncil.oxfordshire.gov.uk/documents/s70847/CMDIDS25042024%20-%20Annex%202%20-%20SATN%20Final%20Report.pdf)
- [OCC Strategic Active Travel Network Stage 1 decision report](https://mycouncil.oxfordshire.gov.uk/documents/s70845/CMDIDS25042024%20-%20Strategic%20Active%20Travel%20Network%20Stage%201.pdf)
- [OCC Stage 1 decision details](https://mycouncil.oxfordshire.gov.uk/ieDecisionDetails.aspx?ID=10387)
- [OCC SATN overview](https://letstalk.oxfordshire.gov.uk/strategic-active-travel-network-satn-yswd)
- [OCC SATN initial consultation](https://letstalk.oxfordshire.gov.uk/satn-initial)
- [OCC SATN final-draft consultation](https://letstalk.oxfordshire.gov.uk/satn)
- [OCC active-travel plans page](https://www.oxfordshire.gov.uk/transport-and-travel/local-transport-and-connectivity-plan/active-travel)
