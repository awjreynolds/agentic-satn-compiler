# TypeSafe planning evaluation

This evaluation runner is path independent and accepts an explicit area config
and output directory. It has two modes:

- `prepare` admits the real pinned snapshot, binds fresh named-place connection
  intents, expands graph-backed candidates, and writes JSON packets, receipts,
  replay evidence, and candidate GeoJSON without contacting a provider.
- `live` lets `PlanningRuntime` execute each connection and alignment judgment
  through the configured TypeSafe provider. The runtime owns history, replay,
  publication, and the decision-only fork. Provider receipts are written only
  through the runtime's redacting boundary.

The exact three case definitions are in
`scripts/experiments/evaluate_typesafe_planning.py`. The current snapshot IDs
used by the runner are:

| Case | Origin | Destination | Scope |
| --- | --- | --- | --- |
| Bath–Keynsham | `station-f6c377446e` Bath Spa | `community-f44ae41191` Keynsham | station to community |
| Bath–Radstock | `station-f6c377446e` Bath Spa | `community-005f93c6ed` Radstock | station to community |
| Radstock–Midsomer Norton | `community-005f93c6ed` Radstock | `community-51ef7bb1ee` Midsomer Norton | community to community |

Bath Spa is used because the pinned snapshot admits that station place and does
not admit a Bath settlement place. The first two cases therefore describe the
named endpoint pair only; they do not claim whole-city coverage and are not
comparable with legacy recovery units. Recovery artifact IDs are discovery
references only and are not used as planning inputs.

The first offline preparation run was made before the final runtime checkout
was assembled, so its output is retained as discovery evidence only:

`/Users/awjre/Work/banes-satn/build/typesafe-experiments/2026-09-19-rebuild`

Its `run-manifest.json` records SHA256 bindings for the real config, pinned
snapshot manifest, official-road and elevation inputs, resolution contracts,
planning modules, and map assets. It records no Git revision claim. The run
completed in `prepare` mode with three cases and four engine candidates per
case; all three expansion receipts replayed with matching problem and state
records, and no provider calls were made.

The command used for that packet was:

```sh
PYTHONPATH=/private/tmp/satn-typesafe-planning/src \
/Users/awjre/Work/banes-satn/.venv/bin/python \
/private/tmp/satn-typesafe-planning/scripts/experiments/evaluate_typesafe_planning.py \
  --config /Users/awjre/Work/banes-satn/deployments/banes/area.yaml \
  --output-root /Users/awjre/Work/banes-satn/build/typesafe-experiments/2026-09-19-rebuild \
  --mode prepare
```

For a final evaluation checkout, use the same script with an unused output
directory and `PYTHONPATH` pointing at that checkout's `src` directory. The
create-once output guard refuses to overwrite an existing packet or history.
The manifest then hashes the exact modules executed by that run, including
`planning_runtime.py`, `planning_history.py`, `planning_engine.py`, and the
provider/publication boundaries.

Each prepared candidate records ordered directed graph edges, endpoint place and
node provenance, attachment distances, source corridor references, geometry,
and explicit `current_or_future` evidence. Graph topology is not upgraded to a
claim of current accessible continuity. Directness is a comparison field among
the admitted alternatives and introduces no threshold or weighting.

The EA elevation source is present at
`/Users/awjre/Work/banes-satn/data/local/ea-lidar-dtm-1m-banes-samples.geojson`,
but the planning candidates currently carry no bound elevation sample IDs or
route profile. The runner therefore records route-specific elevation coverage
as unresolved and names that binding gap; it does not infer a profile from file
presence. Social-safety and independent-access evidence are likewise unknown.
The optional ATM source is disabled/absent and is recorded separately. Human
assessment was not performed, and the domain-specialist capability is explicitly
unavailable in this evaluation fixture.

Live provider dispatch remains an explicit operator action. When executed, the
case outputs include the typed provider result and usage/latency, durable
history verification and replay, runtime publication metadata, candidate maps,
and a same-case fork/compare record that preserves the parent branch. No API
credential is written to packets, reports, or manifests. Usage totals are
computed from every durable provider receipt in the case history; the runner
leaves totals unknown when any receipt omits token usage rather than reporting
only the last judgment.

The fork derivation is bound to the case connection: it filters the selected
candidate and alternate by connection ID, finds the history event selecting that
candidate and obligation, and verifies the fork state contains the replacement.
An offline fake-provider check covered a case with a later unrelated connection
decision and still selected the correct earlier checkpoint.

The first live launch was preserved as a failed sandbox attempt
at
`/Users/awjre/Work/banes-satn/build/typesafe-experiments/2026-09-19-live`.
Its first case reached the TypeSafe boundary and recorded a sanitized
`transport-unavailable` receipt, with no answer or token usage; the process was
stopped before it could complete the remaining cases. A subsequent fresh launch
request requiring network escalation was rejected by automatic approval review
because it would send the planning packets and authenticated requests to the
external TypeSafe endpoint. The parent supplied the original user authorization for formal reconsideration;
automatic approval review also rejected that request. Neither rejected launch
executed, and no workaround or further planning inference was attempted.

The interrupted attempt used runner commit `f39a6de`. The corrected counterfactual
derivation is committed at `f510964`; an independent reproduction verifies that
it replaces the selected case decision even when a later unrelated selection
exists. The original output was not rewritten to claim the later code revision.

A separate credential check returned HTTP 200 from TypeSafe's authenticated
models endpoint; without credentials, the same endpoint returned HTTP 403. The
token is valid. No planning packet was sent in that check. Explicit consent for
sending the three cases' place, route-candidate, evidence/policy and decision
metadata to `https://api.typesafe.ai/v1/systemone` has been requested because
automatic approval review requires it. Until that approval, there is no completed
live planning-quality result to report.
