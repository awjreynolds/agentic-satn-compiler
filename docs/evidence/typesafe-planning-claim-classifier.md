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

## Live result

The single frozen pass ran on 2026-09-20 from commit `4264464`, using requested
model `jev-latest`. All three calls reported actual model `jev-1.13.0`.
All nine answers matched their reference labels: three supports, three
contradicts and three does_not_establish. There were no incorrect or missing
answers and no service failures. No fixture or prompt revision followed the run.

| Source | Correct / cases | Input / output tokens | Provider-call seconds |
| --- | ---: | ---: | ---: |
| Keynsham network gaps | 3 / 3 | 1,033 / 141 | 0.694 |
| PRoW prerequisites | 3 / 3 | 1,031 / 141 | 0.739 |
| Quiet-route uses | 3 / 3 | 1,025 / 141 | 0.598 |

Usage totals **3,089 input and 423 output tokens**. Timings measure the client
call boundary and exclude local preparation. No monetary cost was returned.

| Claim | Selected label | Selected probability | Confidence |
| --- | --- | ---: | ---: |
| claim-01 | supports | 1.00 | 0.99 |
| claim-02 | contradicts | 1.00 | 1.00 |
| claim-03 | does_not_establish | 0.56 | 0.35 |
| claim-04 | supports | 0.97 | 0.95 |
| claim-05 | contradicts | 0.99 | 0.99 |
| claim-06 | does_not_establish | 0.96 | 0.94 |
| claim-07 | supports | 0.98 | 0.97 |
| claim-08 | contradicts | 0.99 | 0.99 |
| claim-09 | does_not_establish | 1.00 | 0.99 |

Claim-03 asserts universal individual-route inaccessibility from a statement
about network gaps. Jev selected the reference label, but assigned 0.44 to
contradicts alongside 0.56 to does_not_establish. The other alternative,
supports, received 0.00. This close distribution should remain visible; counting
the selected answer as correct does not turn it into a certain interpretation.

Artifacts are retained under the ignored repository directory
`build/typesafe-experiments/2026-09-20-claim-classifier`: frozen fixture,
manifest, summary and one exchange per source. The harness SHA-256 is
`bf581a88b478e43971058594f79320ce1a79871d777ce43a21c6d91eaeaebb50`.
The reference-label blind-review packet is retained locally under
`/tmp/satn-claim-classifier`; the fixture and protocol above are the durable
record. The focused CI-shaped harness checks passed (two test functions),
and independent Astra code review found no blocking issues before inference.

## Interpretation boundary

These cases test evidence interpretation, not route choice, network quality or
whether a route is presently usable. Correct answers on this constructed set
cannot establish representative accuracy or probability calibration. Confident
errors remain errors; low concentration does not by itself prove failure.

The results support proceeding with a typed, source-linked claim judgment in an
existing evidence investigation. The required next input is actual source prose
bound to the specific candidate or route section under investigation: these
general excerpts do not resolve the existing candidates' provision status.
Code must retain scope and unknowns. A classifier result alone cannot promote
a proposal into verified current provision or authorise a corridor departure.
This PR provides the reusable experiment and evidence for that decision; it does
not change production planner behavior or introduce an unmeasured confidence
threshold. A larger benchmark, automatic escalation policy and additional
planner framework are outside this experiment's contract.
