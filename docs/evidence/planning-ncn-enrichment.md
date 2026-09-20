# NCN evidence in planning graphs

[Explain missing greenway coverage in retained planning alternatives](https://github.com/awjreynolds/agentic-satn-compiler/issues/485)
identified a missing preparation step. The normal compiler applies
`mark_ncn_edges(network, context)` before constructing `RoadGraph`; experimental
planning constructed its problem and expansion graphs directly from the raw
snapshot. The existing helper binds admitted NCN context to routing edge facts.
A road name or source-inventory attachment is not a substitute for that binding.

[Apply admitted NCN evidence before planning graph construction](https://github.com/awjreynolds/agentic-satn-compiler/issues/488)
reuses that helper in the planning paths. This restores evidence without adding
routing preferences or inferring present access, safety or provision.

## Independent fixed-case measurement

The measurement used the existing enrichment and routing code at `35e4a77`,
before the planning call-site repair. It compared raw and enriched graphs from
`banes-osm-open-roads-v1-2026-07-29`, using the same Radstock–Midsomer Norton nodes
`1545936215` and `4664155942` and the existing `choose_alignment` policy.

| Observation | Raw graph | Enriched graph |
| --- | ---: | ---: |
| Graph edges | 40,745 | 40,745 |
| NCN flags / alignment bases | 0 | 1,546 |
| Direct length | 2.5247365700451523 km | unchanged |
| Strategic-spine length | 2.6139796928980035 km | unchanged |
| NCN-informed length | 2.5247365700451523 km | unchanged |
| Low-traffic length | 2.8533572126233944 km | unchanged |

All four ordered paths and their NCN shares remained identical; each share is
zero. The NCN-informed path still duplicates direct, and the existing mechanical
selection remains strategic-spine. The omission explains missing graph facts,
not the specific candidate outcome or an earlier Jev answer. No weights were
adjusted to force a different result.

The helper retains its established 20-metre buffer and at-least-half overlap
rule. These are existing source-binding semantics, not new policy or surveyed
proof. Input bytes and helper/routing code hashes remained unchanged across the
measurement. No full compilation, history replay or model inference ran.

## Retained evidence

Local artifacts are retained under
`build/typesafe-experiments/2026-09-20-ncn-enrichment/`: `measure.py`, `result.json`
and `REPORT.md`. The result records exact paths, command, input hashes and the
unchanged helper/routing code hashes. Its SHA-256 is
`3269407c2f5b7e7a6d713b1f37f0db59c7eda1b3a8d338f5a6348274de8026d7`;
the reproduction helper SHA-256 is
`bb0d6a89ae7370a9e9a58b472aa84311387c6149de698d9268ba4e2c0f0b4e60`.

This graph-level contrast establishes the expected enrichment effect. Focused
public-seam tests accompany the call-site repair; neither form of evidence
establishes improved network quality or live classifier value.
