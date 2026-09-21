# Bounded Agent Delegation

This repository uses a main agent with narrow specialist worker packages. The
main agent remains accountable for decisions and the integrated result. Concrete
worker models are global configuration details, not repository-level contracts.

## When to delegate

Do not delegate routine questions, small edits, or work whose implementation and
verification are tightly coupled. Delegate when a substantial task contains a
clear investigation, implementation, verification, or documentation package that
can be bounded without transferring the whole conversation.

Use the smallest useful set of roles:

- `bounded_explorer`: read-only investigation of unfamiliar or peripheral code,
  dependencies, tools, logs, or configuration.
- `bounded_executor`: default implementation worker for one owned production surface.
- `bounded_tester`: independent focused tests and failure analysis after an executor
  has completed its smallest relevant check.
- `bounded_doc_writer`: targeted durable documentation based only on verified facts.
- `deep_executor`: exceptional fallback for a cross-cutting package that cannot be
  narrowed effectively after the main agent has investigated it.

## Task capsule

Start a worker with a self-contained capsule of at most 400 words containing:

1. Task ID and desired outcome.
2. Owned files or read-only investigation surface.
3. Acceptance criteria.
4. Relevant context, source, interface, and test paths.
5. The smallest focused validation command or evidence required.
6. Protected and out-of-scope areas.
7. A concise return format.

Workers may inspect adjacent dependencies to diagnose a blocker, but they must not
expand their edit surface. They report the evidence and proposed scope change to
the main agent.

## Execution and repair loop

1. The main agent establishes requirements, package boundaries, and ownership.
2. The executor implements a coherent increment and runs the smallest relevant
   check.
3. The tester independently exercises the acceptance criteria. Broader checks run
   only when required by the repository's delivery-first rules or an external gate.
4. Test or fixture defects stay with the tester. Production defects return to the
   same executor with the reproduction command, expected and actual behavior, and
   affected criterion.
5. The main agent reviews critical hunks and integration boundaries, resolves any
   conflicts, and owns the final response and Git operations.

Do not weaken assertions, claim unrun checks passed, repeat evidence-free repair
turns, or run agents concurrently against overlapping files or shared mutable test
infrastructure.

## Project context and durable decisions

The main agent reads `CONTEXT.md` and relevant ADRs directly. Subagents receive only
the relevant paths and concepts in their capsule. The main agent alone decides
whether verified work changes the domain model or requires an ADR; a documentation
worker may edit those files only when given an explicit, already-resolved decision.

## Durable progress tracking

For delegated or long-running work, the main agent keeps a current progress record
in the existing task artifact directory and links it from the task handoff. Keep
private evidence local; use the GitHub issue for public milestones and remaining
acceptance criteria. Reuse existing records rather than creating another tracker.

The current record contains:

- Objective, acceptance criteria and current milestone.
- Last verified result, check time and supporting artifact or check output.
- Active work: owner, worktree/commit, command or session ID and observed status.
- Completed work, remaining criteria and the next necessary action.
- Checkpoint or receipt locations needed to resume without repeating work.
- A concrete stop reason or external dependency when applicable; otherwise none.

Update the record when work is dispatched, a meaningful result arrives, a command
finishes, or the next action changes. Record failed attempts as evidence alongside
the changed hypothesis or input that justifies another attempt. Preserve prior
checkpoints and model exchanges. Distinguish queued, running, failed and verified
work; elapsed time or an unchanged log alone proves neither progress nor failure.

Before resuming, reconcile the record with agent status, running commands and Git
state. Continue from the verified checkpoint. Completion requires the acceptance
evidence, not a completed subtask or a percentage inferred from effort. When
blocked, record the exact dependency and continue independent authorized work.
