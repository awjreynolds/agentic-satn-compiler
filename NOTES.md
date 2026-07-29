# Workspace Notes

- Work is tracked in GitHub Issues for `awjreynolds/agentic-satn-compiler`.
- Issue #31 is the spine-led Backbone-and-Access Network specification.
- Issues #32–#43 are implementation tickets with native blocking relationships.
- Issue #32 is the current implementation frontier. Completing it unlocks #33, #36 and #40.
- The agreed primary acceptance seam is the public `satn.compile(...)` interface using governed fixture snapshots and inspecting its Compilation Result and published artifacts.
- Implementation uses the `implement` skill, TDD at the agreed seam, a full test run at the integration boundary, and `code-review` before completion.
- The user wants an unattended overnight implementation loop rather than a scheduled recurring process.
- The user authorises one rigorously tested and reviewed pull request per ticket, merged into `main` before the next ticket begins.
- When several tickets are eligible, the user wants the lowest issue number selected.
- The user values completion over preserving a preconceived proof-of-concept outcome, but does not want validation standards lowered.
- The overnight run is outcome-bound rather than attempt-bound: blockers should be diagnosed, repaired and revisited until every ticket is merged.
- Existing uncommitted workspace changes belong to the user and must be preserved.
- Issues #89–#98 are to be implemented as one unattended, one-shot LCWIP
  implementation frontier; the human-assurance pilot in #99 is separate.
- The user wants a SOL-high agent to remain as the durable control loop while
  implementation is delegated to lower-token-cost sub-agents. The current
  runtime does not expose Luna, so fresh Terra-high workers will implement
  tickets unless Luna becomes available before the run.
- The SOL-high controller retains exclusive ticket-selection, quality,
  validation, acceptance, publication and merge authority; implementation
  workers cannot accept or merge their own work.
- Each issue in the LCWIP frontier receives a fresh Terra-high implementation
  worker with a compact controller-authored brief. Workers do not inherit the
  growing frontier conversation; durable state lives with the controller and
  repository.
- If Terra-high is unavailable, a fresh SOL-high worker performs that one task;
  model fallback never weakens the contract or validation bar.
- Each ticket requires controller-reproduced deterministic validation plus
  independent fresh Standards and Spec reviews. Worker-reported tests alone
  cannot accept a ticket, and every blocking finding must be repaired and
  revalidated before publication or merge.
- Every sub-agent gets exactly one bounded task and is then retired. Terra-high
  is used only for implementation and repair. Fresh SOL-high agents perform CI
  diagnosis, Standards review, Spec review, adversarial audit and delegated
  validation; Terra-high agents never validate or accept work.
- The LCWIP frontier is outcome-bound: it should continue until every issue in
  #89–#98 is implemented, merged and the integrated result is validated.
- Routine repository and GitHub operations in the LCWIP frontier are
  pre-authorised and should not create human checkpoints. Permissions should
  remain scoped to the repository, use automatic approval review for eligible
  boundary crossings, and must not grant access to unrelated laptop files.
