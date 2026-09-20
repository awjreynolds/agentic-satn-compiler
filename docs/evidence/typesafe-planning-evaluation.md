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
token is valid. No planning packet was sent in that check. The later approved
run below is the first run that sent the explicitly authorized B&NES planning
cases and metadata to TypeSafe.

## Approved live attempt

The approved run used the final evaluation checkout at commit `d519650` and
wrote to the create-once directory
`/Users/awjre/Work/banes-satn/build/typesafe-experiments/2026-09-19-live-authorized`.
The process was stopped cleanly after the first case when local publication
reported a duplicate mandatory source inventory entry:

`source_inventory repeats planning-corridor-00ee4748d47514f18d96d760361442ea4f40af70e62a4bb777dd476859c0f9`

The persisted Bath–Keynsham runtime result is therefore an honest incomplete
result. It records `reviewable-incomplete` with termination reason
`semantic-no-progress`, no selected alignment, and no departure. Jev answered
two provider attempts using `jev-1.13.0`; both produced the same typed
`request-evidence` operation with an empty target-reference list. The first
left the proposal unresolved. The second left the semantic state fingerprint
unchanged and was recorded as no progress. Durable receipts total 4,333 input
tokens and 134 output tokens (2,163 + 2,170 input; 67 + 67 output). The
provider requests and responses remain in the redacted immutable history
records; this report does not reproduce their bodies.

At interruption, the first-case directory contained 30 files totalling
955,043,513 bytes, including 25 history record JSON files and a
254,283,649-byte runtime result. That is local state and history volume; it is
separate from the provider token totals above.

The run did not reach the second or third case, so it has no live alignment,
counterfactual fork, map publication, or cross-case quality result. The
first-case history and runtime files remain available under the output path
above. At interruption, total elapsed time was about 17 minutes and the stack was in
post-run history verification. The process was interrupted with SIGINT; no later
provider call was made. The earlier prepare packets and fake-provider fork
check remain the available offline evidence for candidate expansion, replay,
and decision-only fork binding. This live attempt does not claim that any
connection or route alignment was selected.


## Completed three-case POC run

The next approved run completed at code revision `01b5c9e`, retaining its original
outputs under
`/Users/awjre/Work/banes-satn/build/typesafe-experiments/2026-09-19-live-poc`.
Its manifest binds the actual source/module/asset bytes; the Git revision here
identifies the frozen checkout used to execute it. The manifest SHA-256 is
`091d956fc066dba425c92f45be28a59f366ca46182b5ac072fb650aa85da6d92`.

| Case | Observed result | Reported input/output tokens |
| --- | --- | --- |
| Bath Spa–Keynsham | Two evidence requests; incomplete map published | 3,766 / 131 |
| Bath Spa–Radstock | Evidence request returned, rejected locally because probabilities totaled 0.99 | 1,753 / 66 |
| Radstock–Midsomer Norton | Connection chosen and four candidates admitted; alignment request rejected with HTTP 400 `max_tokens_exceeded` | 1,760 / 75; HTTP 400 usage unavailable |

All successful HTTP responses reported `jev-1.13.0`. The requested alias was
`jev-latest`; the failed HTTP request supplied no resolved model. Known receipt
usage totals 7,279 input and 272 output tokens. The original manifest's aggregate
is null because not every call reported usage; the invalid-response wrapper also
omitted usage that remains present in its exact response receipt.

All three recorded histories replayed successfully. No live alignment was
selected, so no live selected-alignment fork is claimed. The third case's
separate deterministic counterfactual fixture preserved its parent and replayed
its recorded branch; it is a fixture, not another model decision.

The run took about 1,017 seconds and occupied 1,252,096 KiB including histories,
source data, proposals and publication assets. Its 45,194,454-byte manifest still
embedded repeated replay data. These observations prompted focused POC repairs:
accept the observed complete Choice distribution without renormalizing it,
compact the classifier's route view, reference large report payloads, and refresh
output fingerprints after adding provider-failure diagnostics. The latter
mutation had prevented the two failed-provider cases from publishing incomplete
maps. Original outputs remain unchanged; subsequent evidence must identify the
fixed code and its separate output location.

## Selective rerun after POC repairs

A fresh Radstock–Midsomer Norton run used frozen code `8d42013` and output
`/Users/awjre/Work/banes-satn/build/typesafe-experiments/2026-09-19-live-poc-alignment`.
It completed normally in about 344 seconds. Two `jev-1.13.0` answers requested
more evidence (3,784 input / 137 output tokens), so the runtime stopped at
`semantic-no-progress` without admitting candidates or choosing an alignment.
The compact alignment request was therefore not reached by this fresh run.

The incomplete map published successfully and all five history events replayed
validly. Fork fixtures were explicitly unavailable because no alignment or
alternatives had been admitted. The manifest is 27,429 bytes, with SHA-256
`255a72fb06b982fa5090d6c2f260d1dec586368123f1c17c863c67d7438e7c78`;
the full local output, including retained source/history and map assets, occupies
approximately 430 MiB. This is a new inference path, not a like-for-like storage
comparison or evidence of alignment quality.

## Recorded-checkpoint alignment experiment

The compact alignment packet was tested directly by cloning the retained
Radstock–Midsomer Norton history and resuming its four-candidate state with
frozen production code `8d42013`. The original source history was preserved.
The completed child experiment is at
`/Users/awjre/Work/banes-satn/build/typesafe-experiments/2026-09-19-live-poc-alignment-resume-v2`.
Its `experiment.py` records the standalone harness (SHA-256
`c30e59b9181abd5de71215b64783fe28fb312c42a4551fce147730576351cd9a`),
and `alignment-report.json` contains compact results and artifact paths.

The harness materialized checkpoint
`52bd65d4376f858ee72d752bbbf3a9200ef0be1fea8a6026f19273c657381bc9`
using `store.checkpoint(event_id)`, then forked `compact-alignment` and resumed
through the existing runtime with the retained case's connection options.
An earlier helper attempt passed the event ID directly to `fork` and failed
before any provider call; it is not counted as a live alignment test.

| New child call | Request bytes | Result | Input/output tokens |
| --- | ---: | --- | ---: |
| Initial alignment judgment | 27,590 | Answered: request evidence | 19,291 / 332 |
| Judgment with recorded feedback | 28,194 | Answered: request evidence | 19,576 / 332 |

Both calls resolved to `jev-1.13.0` and were accepted by the service. The
79,943-byte original request's context-limit failure was therefore resolved
for this case. New child usage totals 38,867 input and 664 output tokens;
the inherited connection judgment is excluded from those totals.

The child ended `reviewable-incomplete` at `semantic-no-progress`, with no
selected alignment. Its map and machine-readable network published. Recorded
replay produced the same output fingerprint; the replay report retains two
diagnostics, and history verification returned `valid: true` over eight
records. The cloned parent remained at revision 5 while the child advanced
independently to revision 8. This demonstrates a live judgment from a retained
checkpoint and preservation of the parent, not a successful route selection.

The output occupies approximately 300 MiB. Local history processing took
several minutes; a process sample observed a 2.7 GB peak memory footprint and
heavy data traversal/garbage collection. These remain POC performance limits,
not claims of efficient interactive latency. No new storage or validation
framework was added to address them.

## Delivered POC boundary

The stack demonstrates front-end evidence admission, a Jev-driven mid-end,
recorded decisions and branching, and back-end map publication. Mechanical,
classifier and agent decision classes remain separate from compiler stages.
Full/partial A-road departure rendering and alternate-selection replay are
established by focused fixtures; the real cases establish live connection
expansion and alignment evidence requests. No real alignment was selected.
Specialist escalation is a configured capability with explicit unavailable
outcomes; no live specialist was used here. Network feasibility, optimality,
production adoption and interactive performance remain unproven.
