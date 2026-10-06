# Issue status reconciliation — 30 September 2026

Inventoried all 373 repository issues. Reviewed all 22 open issues, their
complete discussions and the relevant linked completion/decision records. Pull requests were excluded from the issue count. Implementation was
checked against native `main` at `bba7402`; the original preserved working checkout
was not used as evidence of current behavior.

Four delivered open tickets are closed; 18 remain open with explicit remaining
work or owner/environment dependencies. Two closed neighbourhood records receive
a supersession note. Original scopes and comments are preserved. This is a
documentation and status reconciliation, not a claim that the remaining backlog
has been implemented or a whole-codebase audit has been performed.

## Disposition of every previously open issue

| Issue | Disposition | Remaining work or completion evidence |
| --- | --- | --- |
| [#347](https://github.com/awjreynolds/agentic-satn-compiler/issues/347) — Implement retained incremental and parallel compilation | Open: needs-info | Incremental rebuild/cutover is recorded in #354; remaining M4 gate is #353. |
| [#353](https://github.com/awjreynolds/agentic-satn-compiler/issues/353) — Add bounded Apple Silicon parallel execution and auto benchmark | Open: needs-info | Executor and focused tests delivered; unrestricted M4 benchmark and digest-equivalence proof still required. |
| [#373](https://github.com/awjreynolds/agentic-satn-compiler/issues/373) — Plan an MSW whole-codebase necessity review | Open: needs-triage | Confirm/use the explicitly frozen historical snapshot; resolve child planning decisions before audit execution. |
| [#374](https://github.com/awjreynolds/agentic-satn-compiler/issues/374) — Define focused proof and deletion gates | Open: needs-triage | Define contract-specific deletion/proof gates for the confirmed #373 review. |
| [#375](https://github.com/awjreynolds/agentic-satn-compiler/issues/375) — Design the MSW necessity claim ledger | Open: needs-triage | Specify the minimal claim ledger and lifecycle for #373. |
| [#376](https://github.com/awjreynolds/agentic-satn-compiler/issues/376) — Classify the frozen single-snapshot review manifest | Open: needs-triage | Classify the exact frozen tracked manifest; do not silently retarget to current main. |
| [#377](https://github.com/awjreynolds/agentic-satn-compiler/issues/377) — Define the MSW review execution handoff | Open: needs-triage | Execution handoff depends on the scope, ledger, manifest, traversal and proof decisions. |
| [#378](https://github.com/awjreynolds/agentic-satn-compiler/issues/378) — Choose the whole-codebase structural traversal | Open: needs-triage | Resolve structural traversal and cross-boundary synthesis for the confirmed review. |
| [#380](https://github.com/awjreynolds/agentic-satn-compiler/issues/380) — Classify unfinished architecture migration promises | Open: needs-triage | Classify unfinished legacy migration promises against the review contract. |
| [#404](https://github.com/awjreynolds/agentic-satn-compiler/issues/404) — Plan a rational SATN deployment and lighter Pages package | Open: needs-triage | Reconcile current native catalogue/artifacts/evidence; historical package bytes are not a current measurement. |
| [#405](https://github.com/awjreynolds/agentic-satn-compiler/issues/405) — Choose the canonical strategic-network representation | Open: needs-triage | Resolve native public representation and legacy local/review compatibility; Python public packaging already excludes its strategic sidecar. |
| [#406](https://github.com/awjreynolds/agentic-satn-compiler/issues/406) — Choose the detailed evidence distribution model | Open: needs-triage | Choose public/runtime/on-demand/local evidence distribution with licence, offline and failure behavior. |
| [#407](https://github.com/awjreynolds/agentic-satn-compiler/issues/407) — Define the rational public catalogue boundary | Open: needs-info | Resolve WECA/Wiltshire-only intent versus tracked WECA/B&NES/Wiltshire/WMCA catalogue and stable public paths. |
| [#408](https://github.com/awjreynolds/agentic-satn-compiler/issues/408) — Define the lean public deployment artifact contract | Open: needs-triage | Decide the minimum native public artifact, download and provenance contract. |
| [#409](https://github.com/awjreynolds/agentic-satn-compiler/issues/409) — Design the rational Pages release boundary | Open: needs-triage | Current release/render gate exists; revised release/rollback decision depends on #405–#408. |
| [#421](https://github.com/awjreynolds/agentic-satn-compiler/issues/421) — Reset the SATN method around useful interurban routes and delivery priorities | Open: needs-info | Clarify the dictated “Temple Comb” waypoint; retain historical B&NES recovery as historical evidence. |
| [#450](https://github.com/awjreynolds/agentic-satn-compiler/issues/450) — Implement the TypeSafe agentic compiler rebuild | Closed: delivered | Python TypeSafe POC and scoped evaluation delivered (#455/#499); later Rust delivery is #538/#541. No route-quality superiority claim. |
| [#579](https://github.com/awjreynolds/agentic-satn-compiler/issues/579) — Plan classified-road-bounded urban low-traffic neighbourhoods | Open: needs-info | Identification delivered/refined by #584/#586/#614; navigation-review scope remains #583. |
| [#583](https://github.com/awjreynolds/agentic-satn-compiler/issues/583) — Define the first B&NES neighbourhood design review | Open: needs-info | Choose/review the actual rural-entry urban journey and evidenced street/crossing connections. |
| [#607](https://github.com/awjreynolds/agentic-satn-compiler/issues/607) — Demonstrate ATM-derived officer decisions against the B&NES SATN | Closed: delivered | Illustrative ATM officer demonstration delivered through #609/#611/#617/#620/#625. |
| [#608](https://github.com/awjreynolds/agentic-satn-compiler/issues/608) — Establish the ATM source and route roles for an officer-led example | Closed: delivered | Source roles, example authority and exact Strategic geometry/display settled by #609/#620 and subsequent publication. |
| [#610](https://github.com/awjreynolds/agentic-satn-compiler/issues/610) — Choose a readable officer-versus-compiler comparison example | Closed: delivered | Effective and separately inspectable baseline/officer layers delivered through #617/#620/#625. |

## Closed records and historical evidence

The remaining closed issues are retained as dated decisions, experiments or
completion records. Closure is not evidence that an old policy governs the current
compiler. In particular, [#582](https://github.com/awjreynolds/agentic-satn-compiler/issues/582)
and [#584](https://github.com/awjreynolds/agentic-satn-compiler/issues/584) now identify
their earlier whole-road-enclosure rule as superseded by the sourced built-up-area
and distinct-road-frontage rule in #586, with containment corrected in #614. Older
candidate counts remain historical, not current acceptance totals.

The current release inspected was
[`asatnc-ncn-label-provenance-2026-09-29`](https://github.com/awjreynolds/agentic-satn-compiler/releases/tag/asatnc-ncn-label-provenance-2026-09-29),
with successful [Pages run 36496600102](https://github.com/awjreynolds/agentic-satn-compiler/actions/runs/36496600102).
Successful publication does not prove the unresolved catalogue/product decisions
above, nor that fresh inference occurred in that release.

## Current decision explanation

The [decision guide](../concepts/decision-process.md) explains mechanical rules,
the actual town/city versus rural classifier behavior, specialist escalation,
provisional permission, and officer authority. The
[architecture](../compiler-architecture.md) distinguishes current native execution
from retained Python experiments. No confidence gate, new planning policy, new
worker quota or new performance target was introduced during reconciliation.
