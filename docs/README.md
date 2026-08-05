# Documentation

Choose the shortest route for what you need to do.

## Build and operate

| Task | Start here |
| --- | --- |
| Prove a clone can build a map | [Agent quickstart](getting-started/agent-quickstart.md) |
| Reproduce the flagship council map | [B&NES golden path](guides/reproduce-banes.md) |
| Configure another authority or region | [Build a new area](guides/build-a-new-area.md) |
| Package and optionally publish | [Publish a deployment](guides/publish-a-deployment.md) |
| Diagnose a failed or incomplete stage | [Troubleshooting](troubleshooting.md) |

## Understand the product

| Question | Read |
| --- | --- |
| What makes the compiler useful? | [B&NES feature tour](concepts/feature-tour.md) |
| Where may an AI agent act? | [Compiler architecture](compiler-architecture.md) |
| How are alternatives reduced without hiding them? | [Network-core derivation](compiler-architecture.md#3-what-the-network-core-derives) |
| Why can a result contain gaps? | [Stop, validate and restart](compiler-architecture.md#5-the-agentic-stop-validate-and-restart-protocol) |
| What does the published map actually mean? | [Feature tour: network, evidence and provenance](concepts/feature-tour.md) |
| How are terrain and traffic represented? | [Feature tour: evidence-level inspection](concepts/feature-tour.md#8-inspect-terrain-and-traffic-at-the-evidence-level) |
| How do I reproduce and publish a result? | [B&NES golden path](guides/reproduce-banes.md) and [publish a deployment](guides/publish-a-deployment.md) |

## Reference

- [Area Definition and configuration](reference/area-definition.md)
- [Generated artifacts and provenance](reference/artifacts.md)
- [Domain language](../CONTEXT.md)
- [Architecture decisions](adr/)
- [Detailed project background](reference/project-background.md)

## Documentation contract

B&NES is the sole real-world worked example and screenshot source. The small synthetic
fixture exists only to prove installation quickly. WECA may be cited as secondary
evidence of regional scale, but it is not an onboarding path.

Operational pages state their working directory, prerequisites, network needs,
outputs, success signal and failure recovery. Run the drift check after editing them:

```shell
uv run python scripts/validate_docs.py
```
