# Jev planning-comparison source packet

This note supports [Choose a source-grounded comparison for Jev planning value](https://github.com/awjreynolds/agentic-satn-compiler/issues/484). It tests whether the retained **Radstock–Midsomer Norton** sources distinguish the four admitted candidates. Retrieval date for the official sources below: **2026-09-20**.

## Candidate binding result

The retained expansion packet contains four candidates:

| Candidate | Role | `ncn_share` | Exact directed-edge intersection with admitted Greenway sections |
| --- | --- | ---: | --- |
| `planning-candidate-c571776bc49b3dc419d23732b4986bd60c540cd76da862fd35f4f036dc09ab4a` | direct | 0.0 | none |
| `planning-candidate-9ed0af896a13be89941aa026b9d867d5be3a58c25b2892b4f2fbf77330df4324` | strategic-spine | 0.0 | none |
| `planning-candidate-e0e06f6751b5a4ad799af233b5dd03d55a92148d639d5cede67c25cce16c8c35` | ncn-informed | 0.0 | none |
| `planning-candidate-6d4a4004bf6e378682beed8b343eadf8ba7b5dbfc6b52742c9b6c9ad80034038` | low-traffic | 0.0 | none |

This is an exact identity check against the retained source inventory and graph paths, not a spatial tolerance claim. The inventory admits 83 `greenway-cycleway` sections, 60 with graph attachment and 472 unique attached directed-edge identities. None occurs in any of the four candidate edge lists. The direct candidate still has the previously verified partial A367 overlap at `203490192#512caf4c2171dd14027a`; that is not Greenway evidence.

The inputs are `/Users/awjre/Work/banes-satn/build/typesafe-experiments/2026-09-19-rebuild/cases/radstock-midsomer-norton/expansion-receipt.json` (SHA-256 `6c7bdfc57543b8e3d3916efc7447426895f35fca68ca65f3c67daf7ff7e53084`) and `/Users/awjre/Work/banes-satn/build/typesafe-experiments/2026-09-19-live-poc-alignment/cases/radstock-midsomer-norton/runtime/publications/proposal-state-16df4723dc4610c4f0b83c9f652bacfcffd4d862cc63fbd4eaeae81947b6e6e0-09a9054e8c0c27b0/source-inventory.json` (SHA-256 `eb7f7e01358972ec28d4e10258b6ea84bbfc89cb322e00af00d48612a67d34f4`). Reproduction command:

```sh
jq -n --slurpfile inv /Users/awjre/Work/banes-satn/build/typesafe-experiments/2026-09-19-live-poc-alignment/cases/radstock-midsomer-norton/runtime/publications/proposal-state-16df4723dc4610c4f0b83c9f652bacfcffd4d862cc63fbd4eaeae81947b6e6e0-09a9054e8c0c27b0/source-inventory.json --slurpfile exp /Users/awjre/Work/banes-satn/build/typesafe-experiments/2026-09-19-rebuild/cases/radstock-midsomer-norton/expansion-receipt.json '$inv[0] as $i | $exp[0] as $e | ([ $i[] | select(type=="object" and .classification=="greenway-cycleway") | .topology_fact.directed_edge_ids[] ] | unique) as $g | [$e.candidates[] | {candidate_id, ncn_share:.graph_path.ncn_share, greenway_intersection:([.endpoint_provenance.directed_edge_ids[] | select(. as $x | ($g|index($x))) ] | unique)}]'
```

This set is **all admitted `greenway-cycleway` inventory**, not an authoritative identification of the specific Norton–Radstock Greenway named in the Council prose. NCN inventory source IDs and candidate OSM source-edge IDs are different namespaces; their absence from each other does not establish a coverage gap. Independent review confirmed that all 472 attached inventory directed-edge IDs and all four candidate paths resolve in the pinned child-problem graph, and each candidate’s endpoint-provenance path equals its graph-path list. The exact directed-edge comparison is therefore meaningful across these retained artifacts, but it does not prove geographic absence. The internal route-evidence note and pinned artifacts retain the prior A367 proof.

## What the official sources establish

Bath & North East Somerset Council's [Somer Valley Strategic Planning Options (revised 11 March 2024)](https://www.bathnes.gov.uk/sites/default/files/Somer%20Valley%20Strategic%20Planning%20Options%20-%20Revised%2011%20March%202024.pdf), §3.6.7, page 30, names the Midsomer Norton–Radstock Greenway among the main cycle routes, while stating **“there isn’t a comprehensive cycle network for the Somer Valley.”** It also says footpath and bridleway quality and year-round suitability would need investigation. This is corridor context, not a current provision, access, safety, or whole-route judgment for any retained candidate.

The Council's [Midsomer Norton & Westfield Walking, Wheeling & Cycling Links decision report E3518](https://democracy.bathnes.gov.uk/documents/s80462/E3518%20-%20Midsomer%20Norton%20and%20Westfield%20Walking%20Wheeling%20and%20Cycling%20Links.pdf), decision date not before 10 February 2024, §§3.3–3.4, describes proposed connections to the Norton–Radstock Greenway and named streets including First Avenue, Second Avenue, Excelsior Terrace, the B3355 and Charlton Road. It says the proposals would be confirmed through detailed design; its text supplies no geometry or source-section identity that can be joined to a retained candidate. It therefore establishes intervention intent and status, not delivery or candidate suitability.

## Comparison decision

These sources do **not** currently enable a defensible downstream route-quality comparison among the four candidates. A Jev/code result claiming that one candidate is better because it follows the Greenway, or because E3518's proposed links apply to it, would be an unbound inference. First diagnose whether existing admitted topology and candidate generation can supply a meaningfully bound alternative. Additional authoritative section geometry or asset records are needed only if that diagnosis establishes a source gap. An interim experiment can assess evidence handling on the same explicitly bounded source; it cannot establish current continuity or planning quality.

A narrower comparison can test whether a retained claim judgment helps the next planning investigation. Freeze the same candidates, raw source, claim, local scope, policy, question, offered actions and provider configuration. The control receives the raw source and claim; the treatment additionally receives the previously recorded relation, distribution and confidence. A specific, actionable investigation addressing a remaining evidence gap can demonstrate bounded workflow value. An unchanged generic request or unsupported selection does not demonstrate benefit. Neither outcome establishes causality, calibration or real-world route superiority.

The existing runtime cannot yet construct that fair control through its public operations: source prose reaches alignment context inside a completed `evidence_judgments` record, while `request-evidence` has no source-only observation. A direct packet experiment can isolate the judgment without changing production code, but must be reported as experimental rather than an integrated branch comparison. Adding a production evidence abstraction solely for this test is unnecessary. This is also not a Jev-versus-code route-quality comparison: a mechanical shortest path would introduce a preference that the agreed brief does not treat as ground truth.

Before another inference, [Explain missing greenway coverage in retained planning alternatives](https://github.com/awjreynolds/agentic-satn-compiler/issues/485) will establish whether the offered alternatives omit meaningful admitted paths because of topology, binding or candidate generation. The observation above motivates that investigation; it does not yet prove a compiler defect. No further classifier calls were made for this research.
