# Agent Instructions

# MSW — the kernel

## program — complete

```
contract ← the requested outcome + the smallest criteria that prove it

while ∃ claim c : deleting c leaves contract unmet ∨ unproven
      do c ; prove c

halt ; report
```

## definitions — no behavior lives here, only meaning

**contract** — the requested outcome and the smallest set of acceptance criteria that would prove it, stated before any work. The sole source of necessity; a ceiling as much as a floor. If the request is ambiguous: attended → ask; unattended → bind the smallest reading consistent with stated intent and record the assumption.

**claim** — anything petitioning to become work: a plan step, a change, a test, a reviewer's P1, a discovered edge case, your own instinct that one more pass would help. Everything enters as this type. Nothing enters as a verdict.

**deleting c leaves contract unmet ∨ unproven** — the only test. A claim passes solely by breaking the contract — reproducibly, within the task's actual inputs and environment. Severity is derived from the contract, never inherited from whoever raised the claim. *Useful*, *thorough*, and *possible* are not aliases for *necessary*. A claim that fails receives one line in the report — never a fix, an investigation, or a deferred follow-up.

**do ; prove** — the smallest reliable act that closes the gap, and evidence sized to the claim it settles. An unproven act keeps its claim alive; a proven one closes it — and re-proving a closed claim is itself an inadmissible claim.

**halt** — the fixed point: contract proven, no remaining claim passes. Not reviewer silence; not exhausted imagination. Halting before the fixed point and looping past it are the same bug, mirrored.

**report** — the outcome against the contract; the proof; rejected claims worth the user's attention, one line each. Nothing else.

## fuses — outside the program, for when its evaluator fails

```
rounds = 3            → halt anyway ; report open items, do not chase them
claim born in round n+1, visible in round n   → rejected
```

## No unauthoritative limits

Never invent a limit. A cap, threshold, quota, budget, timeout, retry or round count, file or line count, acceptance-criterion count, agent count, or similar constraint is admissible only when its exact value is:

- explicitly required by the requester;
- imposed by an applicable technical or platform contract;
- defined by authoritative project policy; or
- derived from measured evidence necessary to meet or prove the task contract.

State the authority or derivation whenever proposing or applying a limit. If no authority exists, omit the limit and use the MSW necessity test. Metrics may be reported as evidence, but they must not become gates, defaults, targets, or recommendations through agent intuition. Examples and representative proportions never become defaults. If a necessary limit is an unresolved owner choice, ask; do not manufacture a value.

## Delivery-first development

- Do not run audit cycles, broad review loops, or the full test suite by default.
- Implement the requested change, run the smallest focused test or basic check that
  exercises it, fix any concrete failure, and move on.
- Run a full audit, comprehensive review, full generation, or full test suite only
  when the user explicitly requests it or an unavoidable external release gate
  requires it.
- Do not run GitNexus impact, detect-changes, or other audit tooling unless the user
  explicitly requests an audit. GitNexus may still be used for quick code navigation.

## Delegation

- Work directly for routine questions, small edits, and tightly coupled changes.
- For a substantial task with genuinely independent work, the main agent may
  coordinate the global `bounded_explorer`, `bounded_executor`, `bounded_tester`,
  and `bounded_doc_writer` roles. Use `deep_executor` only as a fallback for an
  unusually difficult cross-cutting package.
- Keep the main agent responsible for requirements, planning, task boundaries,
  integration, Git state, user communication, `CONTEXT.md`, and ADR decisions.
- Delegate bounded packages, not the conversation. Each package must name its
  outcome, owned files or investigation surface, acceptance criteria, relevant
  context paths, focused validation, protected areas, and return format.
- Prefer one executor followed by independent verification. Run agents in parallel
  only when their work is read-only or their write surfaces cannot conflict.
- Keep at most three subagents active. Reuse an existing executor for fixes found by
  the tester instead of spawning a replacement without evidence.
- Require concise evidence: changed files, commands and actual results, defects or
  blockers, and the next action. Store long logs under `/tmp`.
- Do not delegate merely to fill concurrency, and do not let delegation trigger the
  broad audits or full-suite checks prohibited above.

See `docs/agents/delegation.md` for role selection and the bounded repair loop.

## Agent skills

### Issue tracker

Issues and PRDs live in GitHub Issues for `awjreynolds/agentic-satn-compiler`. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the default mattpocock/skills triage label vocabulary. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context layout using root `CONTEXT.md` and system-wide ADRs in `docs/adr/`. See `docs/agents/domain.md`.

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **banes-satn** (19325 symbols, 32342 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

## Use for navigation

- When exploring unfamiliar code, `gitnexus_query({query: "concept"})` can locate
  relevant execution flows without a broad codebase review.
- When full context on a specific symbol is useful, `gitnexus_context({name:
  "symbolName"})` can show its callers, callees and participating flows.
- Use `gitnexus_rename` for graph-aware symbol renames instead of find-and-replace.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/banes-satn/context` | Codebase overview, check index freshness |
| `gitnexus://repo/banes-satn/clusters` | All functional areas |
| `gitnexus://repo/banes-satn/processes` | All execution flows |
| `gitnexus://repo/banes-satn/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
