# Greenway retained-alternative counterfactual

This evidence run exercises [Replay a counterfactual choice of the existing
Greenway option](https://github.com/awjreynolds/agentic-satn-compiler/issues/494)
through the existing planning seams. It starts from a copied retained history
containing the already materialized Radstock–Midsomer Norton low-traffic
candidate, forks at the retained main head, and applies one mechanical
`select-alignment` operation. It does not create a candidate, rerun routing,
call a provider, enrich source references, or change the original history.

The source history was copied from
`/Users/awjre/Work/banes-satn/build/typesafe-experiments/2026-09-19-live-poc-alignment-resume-v2/history`
into:

`/Users/awjre/Work/banes-satn/build/typesafe-experiments/2026-09-20-greenway-counterfactual/history`

Independent comparison found all 46 non-lockfiles byte-identical to the
inherited copy, including the retained `main` head
`083eb9365f1a4793dae3c60d1b00e84dbcde219747ed4bd02a21f6470b4cd2dc`. No
pre-copy manifest exists, so this is byte-comparison evidence rather than a
before/after source-hash attestation.

The baseline replay has problem fingerprint
`83ce79cf93b8130d84d0d53eb517d7ea1a17e1d7ac8cf8e87804ee869dc90ab6`, state
fingerprint `c6b573d1a51ff01238e92dc75f0fbfbf4c628724319988bdc8bde27b385600b9`,
9,696 candidates, and zero selected alignments. The retained candidate is
`planning-candidate-6d4a4004bf6e378682beed8b343eadf8ba7b5dbfc6b52742c9b6c9ad80034038`:

- role: `low-traffic`
- ordered directed edges: 20
- recorded path length: 2.8533572126233944 km
- `current_or_future`: `unknown`
- `source_corridor_refs`: `[]` (unchanged)

The candidate’s directed path intersects the admitted named Norton Radstock
Greenway topology. The overlap record identifies source section corridor
`planning-corridor-aa0b50612c23b12d966cf3b324be2ab2a5101ecaf7b97877bb598d1ea5a5c314`,
with source identity `261241204,26624188` and evidence ID
`cycleway-b0c1436e7d85`. Its topology hits path positions 4, 5, and 6 (zero
based); the source geometry exactly equals the graph edge at position 5. The
candidate also touches the adjacent named Greenway corridor records, all
retained in the overlap record. This is source/path overlap evidence only; it
does not promote provision, access, safety, or route quality.

The fork checkpoint is
`52bd65d4376f858ee72d752bbbf3a9200ef0be1fea8a6026f19273c657381bc9`. The
counterfactual branch is `greenway-counterfactual`; its accepted mechanical
event is `01370f399a2d47ce70d1d71344e1a11ef7108adb16120d0b1727806dd870335c`.
The operation was:

```json
{
  "kind": "select-alignment",
  "payload": {
    "candidate_id": "planning-candidate-6d4a4004bf6e378682beed8b343eadf8ba7b5dbfc6b52742c9b6c9ad80034038",
    "obligation_id": "evaluation-radstock-midsomer-norton"
  }
}
```

The result is `reviewable-incomplete`. The selected alignment retains the
same candidate ID, 20-edge path, 2.8533572126233944 km length, and
`current_or_future: unknown`. Offline replay equals the recorded advance
result. Comparing `main` with the counterfactual reports `changed_artifacts: []`
and the expected replacement of the original head event on the branch. The
result summary and publication pointer carry the new output fingerprint
`9bae04387d206910e551c4b4e1b5e21a7d6b15aa2aec0a93b33b04e0e8cb3a91`. The
original main head remains
`083eb9365f1a4793dae3c60d1b00e84dbcde219747ed4bd02a21f6470b4cd2dc`, and all
publication artifact hashes match the manifest.

The generated publication and review map are under:

`/Users/awjre/Work/banes-satn/build/typesafe-experiments/2026-09-20-greenway-counterfactual/publication/`

The compact evidence artifacts are `baseline.json`, `candidate.json`,
`operation.json`, `comparison.json`, `result-summary.json`, and `manifest.json`
in the same experiment directory. The helper is
`build/typesafe-experiments/2026-09-20-greenway-counterfactual/select_existing_greenway_candidate.py`.

Reproduction command, using the isolated checkout imports, is:

```sh
# cwd: /private/tmp/satn-greenway-retained-alternative
PYTHONPATH=/private/tmp/satn-greenway-retained-alternative/src \
  /Users/awjre/Work/banes-satn/.venv/bin/python \
  build/typesafe-experiments/2026-09-20-greenway-counterfactual/select_existing_greenway_candidate.py
```

The command exited 0 and produced output once. A rerun requires fresh history
and output paths. The earlier failed duplicate-admission harness is preserved
separately under
`/Users/awjre/Work/banes-satn/build/typesafe-experiments/2026-09-20-greenway-retained-alternative/`.
It attempted to admit an already materialized candidate; this was a harness
setup issue, not an engine failure.
