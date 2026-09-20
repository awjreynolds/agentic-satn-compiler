# Route-scoped source evidence

This continues [Bind source-backed claim judgments to planning route sections](https://github.com/awjreynolds/agentic-satn-compiler/issues/482)
after the [source-claim classifier experiment](typesafe-planning-claim-classifier.md).
The behavior under test is an existing evidence investigation receiving a
source-linked judgment that survives later planner context and offline replay.
The investigation remains unresolved; the judgment is not a new route status.

```text
pinned candidate section + supplied source prose
  → mechanical scope admission
  → classifier: source/claim relation and uncertainty
  → existing evidence request + retained history
  → next planning context / offline replay
```

## Actual source binding

The [B&NES E3519 report](https://democracy.bathnes.gov.uk/documents/s81370/E3519%20-%20Somer%20Valley%20Links%20Strategic%20Corridor%20Project.pdf),
section 1.2 on page 1, describes the Somer Valley Links project's travel-improvement
aim and names Radstock, Midsomer Norton and the A367. The cover gives a decision
date of **not before 4 May 2024**, not a publication or completion date. Retrieval
was on 2026-09-20. The fixture preserves its short exact excerpt.

The retained direct candidate
`planning-candidate-c571776bc49b3dc419d23732b4986bd60c540cd76da862fd35f4f036dc09ab4a`
belongs to `evaluation-radstock-midsomer-norton`. Its graph path contains
51 directed edges and is approximately 2.525 km long. The bounded overlap is
directed edge `203490192#512caf4c2171dd14027a`, ordered edge index 4,
from node `2678501462` to `1444761739`.

That edge overlaps the pinned A367 inventory section
`planning-corridor-10936e578da7c3ee8d0cf03f79dabfb2879bd17e74db4d40d6cc9ae6c4d0af22-section`.
The candidate and graph coordinates match as stored at seven decimal places;
the inventory's nine-decimal endpoints match when represented at that same
precision. This uses the artifacts' representation precision, not an invented
spatial tolerance. Shared OSM source ID `203490192` alone would be insufficient:
it occurs in five directed edges of the candidate.

The [fixture](../../tests/fixtures/typesafe-route-evidence.json) records the exact
identities, endpoint comparison and hashes of the candidate, inventory and graph
artifacts. This is **corridor context only**. The prose does not place works on
that exact edge, establish dedicated cycling provision there, or cover the
candidate's remaining edges. Its `current_or_future` stays `unknown`.
Independent Astra review verified all three artifact hashes, the fixture hash,
edge index and coordinate comparison against the pinned records. That check
establishes the partial geometry binding, not the truth of a provision claim.

## Frozen questions and interpretation

The reference packet contains two claims sharing the source: whether the passage
describes the named travel-improvement aim, and whether it establishes dedicated
existing cycling infrastructure on the overlapping section. Independent blind
Astra review classified them as `supports` and `does_not_establish`, respectively,
before inference, agreeing with the frozen labels and flagging no ambiguity.
This is a model-reviewed source interpretation, not expert field ground truth.

The live integration investigates only the second claim, because the existing
evidence-request seam asks one specific question. The positive claim documents
what the source does establish; repeating its already-demonstrated classifier
behavior is unnecessary. Focused behavioral tests separately prove that even a
`supports` judgment leaves the overall request and route status unresolved.
The fixture SHA-256 is
`eda133f15c486ead65d59f826b2306ea2a317910b313e4ca961ab7e5b21b06de`.

Reference labels and source-grounded rationales are held out of model inputs.
The binding proof and its interpretation limits are also held out: Jev must
classify the supplied passage, not repeat a prewritten conclusion. The model
receives the public source and claim. Candidate, corridor, section and edge
identifiers stay local, bound to the request and retained judgment. Scope
admission remains code's job; Jev does not infer a spatial join from road names.

No judgment clears a request or promotes a whole candidate to current provision.
The source, scope, typed relation, distribution and confidence remain inspectable.
There is no new confidence threshold, general evidence framework or caller input
version. Live source interpretation and behavioral integration evidence are
reported separately from route quality or field verification.
