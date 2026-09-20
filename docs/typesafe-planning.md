# Experimental TypeSafe compiler

The planning path separates evidence admission, network decisions and publication. Compiler stages and intelligence capabilities are separate concepts: code, Jev and a configured specialist participate where their capabilities fit the task.

Each recorded planning decision carries a `decision_class`, independently of its compiler stage and event kind:

| Decision class | Meaning |
| --- | --- |
| `mechanical` | Deterministic rules, computation, admission or validation. |
| `classifier` | A typed Jev judgment over the supplied evidence and choices. |
| `agent` | A proposal from a configured reasoning or specialist capability. |

The actual provider and model identity remain recorded alongside this class. The compiler derives the class from the capability that performed the work; a response cannot grant itself a different class. Mechanical validation of a classifier or agent proposal records the validation separately and preserves the proposal's original class.

```mermaid
flowchart TD
    Sources[Governed snapshot + planning brief] --> Front[Front end: admit and bind evidence]
    Front --> Problem[Planning Problem]
    Problem --> Mid[Mid-end: schedule a focused decision]
    History[Recorded decisions and materialized evidence] --> Mid
    Mid --> Analysis[Code-owned topology and evidence analysis]
    Analysis --> Judgment[Jev or configured specialist]
    Judgment --> Operation[Proposed typed operation]
    Operation --> Verify[Verify scope, bindings and invariants]
    Verify --> State[Child Proposal State]
    State --> History
    State --> Mid
    State --> Validate[Validate output and account for unresolved obligations]
    Validate --> Back[Back end: map and machine-readable exports]
```

## Stage contracts

| Boundary | Responsibility | Required proof |
| --- | --- | --- |
| `build_planning_problem` | Lower the pinned snapshot and explicit brief into source corridors, named places, obligations and retained geometry/topology facts. | Every in-scope A-road remains accounted for, including source-only corridors. Source designation never proves usability. |
| `apply_operation` / `admit_expansion` | Accept only a permitted transformation of the correctly bound parent state. | Check identifiers, task scope, evidence, geometry, topology and policy; reject stale or fabricated bindings. |
| `PlanningRuntime` | Schedule decisions and record their exact inputs and outcomes. | Persist the task packet and started attempt before dispatch; feed current decisions and unknowns into later tasks; stop on genuine semantic no-progress. |
| `validate_proposal` | Produce the output contract and truthful completeness status. | A selected obligation needs an admitted selection or connection proof. Missing evidence remains explicit. |
| `project_planning_output` / `publish_planning_output` | Lower validated state into presentation and an immutable artifact bundle. | No new selection or departure decisions; verify output identities and artifact closure before moving the current pointer. |

The proposal is the sole authority for selected alignments. A-road source membership does not force its exact alignment into the proposal. A deliberate full or partial departure requires affected original geometry, evidence, reason and an explicit alternative or unresolved/no-loss outcome. An alternate outcome refers to an already selected admitted alignment. The default map must show the affected source sections prominently, with branch attribution and accessible legend text. Unknown provision is distinct from current provision and future work.

## Applying compiler design principles

SATN's intermediate representations describe a planning problem and a proposed network. Transformations may deliberately change the proposal; they must preserve the brief, governed evidence and mandatory source accounting. Structural and reference checks precede semantic and geometry checks, following the ordering described by [MLIR's operation verification](https://mlir.llvm.org/docs/DefiningDialects/Operations/#verification-ordering).

Analyses compute facts without choosing a new network. Transformations consume those facts and explicitly change state. Derived results may be reused only under their recorded dependencies, following the separation of transformations and preserved analyses in [LLVM's pass manager](https://llvm.org/docs/NewPassManager.html). A configured model can propose an operation but cannot upgrade missing evidence, widen its task scope, or create policy authority.

Failures retain diagnostics and the prior valid state/publication. The retained input, request, response and operation allow the failing decision to be reproduced, paralleling [MLIR's pass failure and reproducer mechanisms](https://mlir.llvm.org/docs/PassManagement/#crash-and-failure-reproduction). These principles are applied directly to the existing Python implementation.

## Replay and new decisions

Recorded replay consumes retained operations and expansion receipts without model calls, source reloads or retrieval. A fork begins before the selected decision; an explicit advance validates the replayed prefix and applies a replacement on the child branch. Changed evidence or policy requires a new binding and invalidation of affected descendants. The original pinned branch remains intact.

A fresh model call may produce a different judgment. Therefore reproducibility means reproducing the recorded compilation from its receipts, not expecting another inference to repeat the same answer. Branch comparison shows changed decisions and dependent results; ancestry alone does not prove that one decision caused a real-world failure.

## Review and evaluation evidence

Implementation and review evidence are tracked in [the rebuild epic](https://github.com/awjreynolds/agentic-satn-compiler/issues/450). The protected historical version is `PreTypesafe`. Focused fixtures establish state, departure-map and replay behavior; the separate pinned B&NES experiments establish what happened on real inputs. Neither establishes feasibility, adoption or an optimal network. Live specialist behavior requires a configured adapter; protocol fixtures must be identified as fixtures.

## Experimental CLI

Use an explicit history directory and a separate output directory for each experiment. `satn plan run CONFIG --root HISTORY --output-root OUTPUT` performs deterministic admission by default. This retains source obligations and unknowns; it is not an intelligently selected network.

For a configured live Jev run, add `--mode live --connection-options OPTIONS.json`. The options file supplies admitted named-place connections; the experiment runner derives their identifiers from the pinned input. Supply `TYPESAFE_API_KEY` through the environment rather than command-line arguments or tracked files. A live run does not imply that a specialist is configured.

```text
satn plan verify HISTORY --branch main
satn plan replay HISTORY --branch main
satn plan fork HISTORY CHECKPOINT --branch alternative
satn plan advance HISTORY OPERATION.json --branch alternative --expected-head HEAD --output-root ALTERNATIVE_OUTPUT
satn plan compare HISTORY --base-branch main --branch alternative
```

`fork` selects a retained pre-decision checkpoint. `advance` consumes an explicit typed operation against the replayed child state; it does not silently rerun inference. Inspect the recorded state and head when constructing the replacement. The output directory contains `run.json`, `proposal.json`, immutable `publications/<output_id>/review-map/index.html`, and `current.json`. Serve the publication directory to review its map and machine-readable exports.

## Architectural review

The implementation review traced the actual boundaries: `planning_engine` admits the snapshot and owns the planning IR and verified transformations; `planning_runtime` schedules and records judgments; `planning_publication` projects validated output and protects publication identity. Compiler stage and decision class are independent. The core has no provider, runtime or publication imports, and the publisher does not choose routes or call providers.

Independent review reproduced and verified fixes for geometry identity, expansion receipts, parent binding, semantic no-progress, obligation completeness, scoped specialist proposals, expanded-checkpoint replay, current-state feedback and multiple connection scheduling. Publication reproductions verify stale-output rejection and preservation of the last valid pointer when a historical bundle is damaged. Browser evidence verifies default full/partial departure geometry and matching legend styles. Focused tests are recorded in the stacked PRs; this was a bounded architectural and contract review, not a claim that every legacy compiler path was audited.
