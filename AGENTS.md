# Agent Instructions

## Delivery-first development

- Do not run audit cycles, broad review loops, or the full test suite by default.
- Implement the requested change, run the smallest focused test or basic check that
  exercises it, fix any concrete failure, and move on.
- Run a full audit, comprehensive review, full generation, or full test suite only
  when the user explicitly requests it or an unavoidable external release gate
  requires it.
- Do not run GitNexus impact, detect-changes, or other audit tooling unless the user
  explicitly requests an audit. GitNexus may still be used for quick code navigation.

## Agent skills

### Issue tracker

Issues and PRDs live in GitHub Issues for `awjreynolds/banes-satn`. See `docs/agents/issue-tracker.md`.

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
