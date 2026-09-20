# Source-backed planning claim classification

This experiment addresses [issue 480](https://github.com/awjreynolds/agentic-satn-compiler/issues/480):
can Jev classify the relationship between supplied planning evidence and a
specific claim? Code supplies the evidence and candidate meanings; Jev returns
`supports`, `contradicts`, or `does_not_establish`, with probabilities and
confidence. This follows TypeSafe's [citation-check pattern](https://docs.typesafe.ai/cookbooks/citation_check).

```text
official source excerpt + claim
  → Jev: supports | contradicts | does_not_establish
  → code: retain judgment and source reference
  → compare against reference label withheld from Jev
```

## Frozen protocol

The [fixture](../../tests/fixtures/typesafe-planning-claims.json) contains three
official B&NES source contexts. Each exercises support, contradiction and an
unsupported inference, giving nine constructed challenge cases. This count
comes from the covered meanings, not a sample quota or an accuracy target.

| Context | Source and locator | Distinction exercised |
| --- | --- | --- |
| Network gaps | [Keynsham and Saltford](https://www.bathnes.gov.uk/creating-sustainable-communities-keynsham-and-saltford), Active Travel Network issues | Network gaps versus universal individual-route inaccessibility |
| Conditional works | [Active Travel Masterplan](https://www.bathnes.gov.uk/sites/default/files/Active%20Travel%20Masterplan_3.pdf), table 8.1, routes 4 and 6 | Required TRO and landowner negotiation versus completed approvals |
| Permitted uses | [Improvements by location](https://www.bathnes.gov.uk/summary-improvements-location), Paulton / Old Mills Lane | Permitted activities versus safety and whole-route continuity |

The fixture retains exact short excerpts, locators and retrieval date
2026-09-20. Titles are descriptive labels. Publication dates were not established
and remain null; retrieval dates do not imply publication or current validity.
Classification concerns only the supplied passage, title and stated date.
Neither omission nor uncertainty alone means contradiction.

Before inference, an independent Astra review received the source contexts,
claims and classification definitions without the proposed labels or rationales.
Its labels agreed with all nine source-derived reference labels, with no
ambiguities flagged. This is independent model review, not expert field ground
truth. The frozen fixture SHA-256 is
`4072550478a146ffe5cb6dc1408030a618fd764c16c244b26eb875fbf407d651`.

The live pass makes one request per source, batching the independent claim
questions over shared evidence. Expected labels, rationales and source locators
are excluded from model state. The existing TypeSafe client supplies credential
handling and response validation. Preparation is offline by default; `--execute`
explicitly requests inference. Output directories are create-once.

Each source exchange is retained with its request and response receipts. Compact
results record choices, distributions, confidence, actual model, usage, elapsed
provider-call time and exchange references. Fixture and harness hashes bind the
run. There is no confidence gate, prompt tuning or repeat-until-correct loop.

From the repository root, prepare the requests in a fresh output directory:

```sh
PYTHONPATH=src python scripts/experiments/evaluate_typesafe_claims.py \
  --fixture tests/fixtures/typesafe-planning-claims.json \
  --output-root build/typesafe-experiments/claim-classifier-prepared
```

For a live pass, supply `--execute` and a different output directory. The client
reads `TYPESAFE_API_KEY` or its existing local credential file. Inspect the saved
`summary.json` and referenced exchanges without rerunning inference.

## Interpretation boundary

These cases test evidence interpretation, not route choice, network quality or
whether a route is presently usable. Correct answers on this constructed set
cannot establish representative accuracy or probability calibration. Confident
errors remain errors; low concentration does not by itself prove failure.

The smallest prospective integration is to attach a typed, source-linked claim
judgment to an existing evidence investigation. Code must retain its scope and
unknowns. A classifier result alone cannot promote a proposal into verified
current provision or authorise a corridor departure.
