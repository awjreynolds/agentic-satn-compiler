# TypeSafe follow-up comparison preparation

This is the offline preparation artifact for issue 487. It is an
experiment-level contrast of two otherwise identical planning packets. It is
not an integrated planner comparison and it makes no route-quality or causal
claim.

## Prepared arms

Both arms are derived from the retained four-candidate Radstock–Midsomer
Norton alignment packet. They keep the same candidate paths and edge facts,
connection options, policy, brief, alignment question, input binding, task
identity and resolved model (`jev-1.13.0`). Both also carry the same raw
source excerpt, claim-02 text, and mechanically admitted local scope. The
fixture's expected label, rationale and binding proof are excluded.

The control removes the already recorded evidence judgment. The treatment
retains that judgment exactly as recorded: `does_not_establish`, probabilities
`supports: 0.0`, `contradicts: 0.0`, `does_not_establish: 1.0`, and confidence
`1.0`. The judgment is inherited from event
`dc24b25927269edf31d5d59aa67d9680a4b5e6f53b9012d4aed8895584258c69` and receipt
`25115198e3cdb9ce291d3a6fedd11818977b0157bac198b3ce28a9dfa65020d8`, produced
by `jev-1.13.0` with 934 input and 48 output tokens. No source was classified
again.

The only packet changes are the two retained-judgment locations:

```text
state.feedback_unknowns[*:route-evidence-claim-02].evidence_judgments
state.unknowns[*:route-evidence-claim-02].evidence_judgments
```

The self-check removes those two values from both arms and verifies that all
remaining state is equal. It also verifies that both arms retain the raw
source, claim and scope, while only treatment retains the relation,
probabilities and confidence.

## Reproducibility

The preparation helper is an ignored experiment artifact at
`build/typesafe-experiments/2026-09-20-jev-followup-comparison/prepare_comparison.py`.
It reads the frozen fixture and retained history, never invokes TypeSafe, and
refuses to overwrite an existing output. The exact preparation command was:

```sh
PYTHONPATH=/private/tmp/satn-jev-followup-comparison/src \
  /Users/awjre/Work/banes-satn/.venv/bin/python \
  /Users/awjre/Work/banes-satn/build/typesafe-experiments/2026-09-20-jev-followup-comparison/prepare_comparison.py
```

It produced the create-once files under
`/Users/awjre/Work/banes-satn/build/typesafe-experiments/2026-09-20-jev-followup-comparison/`:

| arm | canonical request bytes | SHA-256 |
| --- | ---: | --- |
| control | 30,523 | `4645239944a208d52171b7753beb05bf877c80fd1d663e85ff336def86b35378` |
| treatment | 33,087 | `155b410818e74e57e18e74fc94778d0e0c25a2eb42eb71a88dc44415a03a9725` |

The summary is `comparison-preparation.json`; the exact saved envelopes are
`control-request.json` and `treatment-request.json`. The preparation ran with
zero provider calls. The code binding was `9b4634a4bc28dcfc822eca587e495931b64b5762`;
the fixture SHA-256 was
`eda133f15c486ead65d59f826b2306ea2a317910b313e4ca961ab7e5b21b06de`.

The source and claim provenance comes from the frozen
`tests/fixtures/typesafe-route-evidence.json`; the retained packet and
receipt remain under the earlier route-evidence history tree. Their paths and
hashes are recorded in `comparison-preparation.json` rather than duplicated
here.

## Interpretation fixed before launch

The comparison asks whether retaining this scoped source judgment changes a
specific, actionable next investigation in the planning workflow. A changed
action is useful only when it carries the source-specific claim and scope into
a concrete follow-up. An unchanged generic evidence request is no benefit for
this comparison, and an unsupported candidate selection is not a useful
planning consequence. Either arm may remain unresolved.

This is a historical packet contrast. It does not evaluate current NCN
enrichment, repair the recorded edge facts, establish current provision or
field conditions, or compare route quality. The candidate and directed-edge
facts are frozen from the retained packet before any later enrichment; they
remain common input in both arms.

## Validation and launch state

The first self-check was red because the retained judgment used
`candidate_id` and `directed_edge_ids`, while the fixture uses the plural
`*_refs` names. The helper now normalizes those equivalent scope spellings;
the preparation completed green. The focused checks were:

```text
python -m py_compile .../prepare_comparison.py                 PASS
ruff check .../prepare_comparison.py                           PASS
python -m py_compile .../dispatch_comparison.py                PASS
ruff check .../dispatch_comparison.py                          PASS
```

The minimal operator is the ignored
`build/typesafe-experiments/2026-09-20-jev-followup-comparison/dispatch_comparison.py`.
Its default dry-run uses a local fake transport and proved both canonical
request bodies were sent exactly once, with no credential lookup and no
provider call:

```sh
PYTHONPATH=/private/tmp/satn-jev-followup-comparison/src \
  /Users/awjre/Work/banes-satn/.venv/bin/python \
  /Users/awjre/Work/banes-satn/build/typesafe-experiments/2026-09-20-jev-followup-comparison/dispatch_comparison.py
```

The reviewed live command is explicit and requires a fresh create-once output
directory:

```sh
PYTHONPATH=/private/tmp/satn-jev-followup-comparison/src \
  /Users/awjre/Work/banes-satn/.venv/bin/python \
  /Users/awjre/Work/banes-satn/build/typesafe-experiments/2026-09-20-jev-followup-comparison/dispatch_comparison.py \
  --execute \
  --output-root /Users/awjre/Work/banes-satn/build/typesafe-experiments/2026-09-20-jev-followup-comparison/dispatch-live-2026-09-20
```

Only `--execute` creates a credential-reading client; the existing
`TYPESAFE_API_KEY` environment variable or `~/.config/typesafe/api-key` path
is read by `TypeSafeClient`, and the key is never printed or persisted. The
operator sends the two saved envelopes through the existing client validation,
transport and receipt-redaction path, asserting that each outgoing body still
matches its frozen SHA-256. It writes one redacted request/response receipt,
typed answer, actual model, usage record and latency for each arm, including
service failures, and performs no automatic repeat. The requested model is
`jev-1.13.0`; actual model identity is recorded per response.

The frozen launch was submitted after independent review and rejected by
automatic approval review before process execution. Neither request ran.
The reviewer requires explicit user approval for these project-derived
candidate/path and policy packets and the TypeSafe destination; general
experiment authorization was not accepted for this payload. The exact
rejection state is retained in `dispatch-approval-block.json`. There is no
new provider receipt, token usage or downstream outcome to report.

The packets remain unchanged and reviewable. Live dispatch is pending explicit
approval; the local fake-transport checks are the completed execution evidence.

The frozen dispatch helper SHA-256 is
`078893cdb90e199122ad1457b30f4c6550f12ae0e3eac04db77226c76e106852`;
the preparation helper SHA-256 is
`845b59c296a4ad03698a95221ab5f991373a704c5607d3d8273c14642629551b`.
Independent Astra review accepted the packet difference and the operator; its
fake transport confirmed exact bodies and no credential or provider access.
