# Radstock–Midsomer Norton decision probe

This experiment implements the protocol recorded in
[Distinguish task framing, missing evidence and policy in Jev alignment judgments](https://github.com/awjreynolds/agentic-satn-compiler/issues/473).
It uses the retained four-candidate checkpoint from the
[earlier planning evaluation](typesafe-planning-evaluation.md), rather than
regenerating candidates or replaying the whole network.

The historical alignment answers requested evidence without identifying a
specific claim. Inspection also found that available source road attributes
were missing from the model's input. Those observations establish defects in
the supplied context and feedback; they do not explain Jev's earlier reasoning.

The protocol separates three questions, running later probes only if needed:

```text
original question + source-supported road facts
  └─ unresolved → same facts/options + explicit provisional-proposal wording
       └─ unresolved → named evidence dependency or explicit unresolved outcome
```

Candidate identities, endpoints, ordered paths and the existing owner brief
remain fixed. Null source attributes remain unknown. A mapped cycleway does
not prove present access or safety; an available elevation file does not prove
a route-bound gradient profile. A-road corridor accounting and visible
departures remain requirements.

Each inference is a new observation. A changed choice is not proof of causality
or better network quality, and a dependency answer is a new judgment rather
than an explanation of a previous answer. No policy weights, confidence gate
or repeat-until-selected procedure is introduced.

## Execution

The initial probes ran from commit `33c7111`. The source join resolved all
74 directed edges for the four retained candidates. Of those edges, 3 have
an access value and 71 have null access; this does not establish area-wide
coverage or whether any route is currently usable.

| Probe | Request bytes | Actual model | Result | Input / output tokens |
| --- | ---: | --- | --- | ---: |
| Faithful facts, original question | 48,775 | `jev-1.13.0` | `__needs_evidence__` | 31,111 / 332 |
| Explicit provisional-proposal framing | 49,241 | `jev-1.13.0` | `__needs_evidence__` | 31,197 / 332 |
| Named dependency | 60,241 | No answering model reported | HTTP 400 `max_tokens_exceeded` | Not reported |
| Same dependency after reference deduplication | 52,678 | `jev-1.13.0` | `current-future-provision` | 32,730 / 121 |

Provider boundary times were 1.174, 1.219 and 0.894 seconds respectively;
they exclude local source preparation. The first two answers have confidence
0.44 and 0.40 respectively. These values are observations, not decision gates.

The actual answering model matches the historical `jev-1.13.0` receipts.
Fresh inference and the explicitly recorded edge-list-to-ID-map representation
change still limit causal interpretation. The candidate paths, original edge
identities/topology, original options and brief survived compaction. The
wording probe changes both copies of its question consistently.

The first two probes did not identify a selected alignment or a specific
missing claim. The rejected dependency request supplies no planning judgment.
Its failure led to removing duplicate catalog edge lists already represented
by the candidate paths, while preserving the full catalog in the artifacts.
That repair ran from commit `1d74695`; the earlier probe bodies remained
byte-identical and independent review verified all catalog claims, scopes,
coverage, investigations and outcomes survived.

The accepted dependency answer identifies **whether each proposed alignment
is current provision or future intervention**, scoped to the four candidates
and their connection. All four carry `current_or_future: unknown`. Its mapped
investigation is to bind explicit current-provision or future-intervention
evidence. Confidence was 0.79 and provider-boundary time was 1.255 seconds.
Known successful-call usage totals 95,038 input and 785 output tokens; the
rejected request's usage was not reported, so an all-attempt total is unknown.

The resulting decision is recorded in the
[experiment resolution](https://github.com/awjreynolds/agentic-satn-compiler/issues/473#issuecomment-5746107285).
Source-fact preservation is justified as a context-correctness change, without
claiming it makes Jev choose a better alignment. The next investigation is
[Resolve current-versus-future evidence for graph-generated planning candidates](https://github.com/awjreynolds/agentic-satn-compiler/issues/474).
Proposal intent must be distinguished from current provision and intervention
requirements; relabelling every proposal as a future intervention would not
supply evidence. The selected dependency is a useful investigation target,
not proof that resolving it is a prerequisite for any provisional preference.

Artifacts are under the ignored repository directory
`build/typesafe-experiments/2026-09-20-decision-faithful-facts`,
`2026-09-20-decision-framing`, `2026-09-20-decision-dependency` and
`2026-09-20-decision-dependency-compact`.
Each retains the exact redacted exchange, source bindings, field differences,
code/source hashes and typed result. Earlier artifacts remain unchanged.

The focused checks cover unchanged original paths, consistent probe questions,
catalog answer mapping and the real source join for two road segments sharing
one OSM ID. The original identity-preservation regression failed with
`KeyError: 'source_edge_ids'`; after repair, both tests passed independently.
