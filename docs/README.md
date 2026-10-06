# Documentation

The current implementation is the **native Rust compiler**. Python workflows,
research and earlier evaluation results remain available as historical reference;
they are not a statement of native feature parity or the contents of every release.

## Understand the network and its decisions

| Reader or question | Start here |
| --- | --- |
| Planning officers, transport practitioners and general readers | [Decision process and mechanical rules](concepts/decision-process.md) |
| AI readers: what is code, classification or reasoning? | [Decision classes and escalation](concepts/decision-process.md#the-decision-process) |
| Software readers: which module owns each step? | [Current compiler architecture](compiler-architecture.md) |
| What do map layers and unknowns mean? | [Feature tour](concepts/feature-tour.md) |
| What does a candidate neighbourhood establish? | [Candidate neighbourhood evidence](guides/candidate-neighbourhoods.md) |
| How are bus routes and interchange evidence represented? | [Bus-route overlay](guides/bus-route-overlay.md) |
| Which work is delivered or still open? | [Issue reconciliation, 30 September 2026](planning/issue-status-2026-09-30.md) |

## Build and operate the native compiler

- [Clone to a native map](getting-started/agent-quickstart.md).
- [Native build, modes, outputs, replay and officer scenarios](../rust/README.md).
- [Reproduce B&NES](guides/reproduce-banes.md), [prepare another area](guides/build-a-new-area.md), and [native configuration](reference/area-definition.md).
- [Clean native elevation preparation and live-model approval](guides/native-clean-build.md).
- [Package and publish a deployment](guides/publish-a-deployment.md).
- [Native troubleshooting](troubleshooting.md) and [contributing/focused validation](../CONTRIBUTING.md).

Real-world builds require pinned source snapshots and any configured elevation or
built-up-area inputs. Those caches are not supplied by a clone. Mechanical mode
needs no model; live mode and offline replay are different paths with different
receipts. Use the native instructions before copying a historical Python command.

## Retained Python workflows

| Task | Reference |
| --- | --- |
| Small offline installation fixture | [Retained fixture in the quickstart](getting-started/agent-quickstart.md) |
| Configure the retained Python Area Definition | [Historical configuration reference](reference/area-definition.md#retained-python-area-definition-reference) |
| Inspect earlier artifact contracts | [Generated artifacts](reference/artifacts.md) |
| Investigate the Python pipeline | [Historical architecture](compiler-architecture.md#historical-implementations) |
| Understand the earlier TypeSafe prototype | [Python TypeSafe experiment](typesafe-planning.md) |

## Evidence and decisions

[CONTEXT.md](../CONTEXT.md) defines domain terms; it is not a checklist of delivered
features. [ADRs](adr/) record decisions and amendments. In particular,
[ADR 0028](adr/0028-rust-mechanical-compiler-and-compact-decisions.md) records the
native compiler and later access refinements.

[Evidence reports](evidence/), [research](research/), [benchmarks](benchmarks/),
the [September method reset](planning/satn-method-reset.md), and
[project background](reference/project-background.md) preserve dated findings.
Their proposals, timings, provider outcomes and local build status apply to their
stated inputs and revision. They must not be read as current release guarantees.
The [image inventory](images/README.md) identifies historical screenshots.

B&NES is the worked planning example. WECA, Wiltshire and West Midlands also have
source or deployment configurations; an available configuration does not prove
all current native features or every evidence source for that area.

For canonical page links and the retained Python configuration example, run:

```sh
uv run python scripts/validate_docs.py
```

That check validates documentation structure, not current behavior or geographic
quality. Documentation changes also need comparison with the relevant source and
focused implementation tests; they do not require fresh live model calls.
