# Native publication artifacts

The Rust compiler has two output shapes. `--mode mechanical` creates a local
inspection bundle. Live planning and replay create the decision-map publication
used for native agentic review.

## Mechanical output

| Artifact | Contents |
| --- | --- |
| `index.html` | Interactive map of admitted source corridors, mechanically prepared candidates, and evidence. |
| `summary.json` | Mechanical compilation report, source inventory, prepared connections, candidates, and diagnostics. |
| `network.geojson` | Portable feature collection for GIS inspection. |

This bundle has no `publication.json` and is not by itself a native-agentic Pages
publication.

## Live and replay publication

| Artifact | Contents |
| --- | --- |
| `index.html` | Interactive decision map. |
| `decision-map.json` | Compact decision manifest, counts, decisions, departures, and source references. |
| `decision-map.geojson` | Map features and geometry for the published decisions and evidence. |
| `publication.json` | Public publication identity, status, counts, attributions, and artifact paths. |
| `planning.json` | CLI result from live planning or replay. |
| History directory | Retained base, task, attempt and typed-operation records; set with `--history`, otherwise under the output directory. Replay reads existing history rather than creating fresh provider receipts. |
| `officer-scenario.json` | Present when replay applies an officer decision ledger. |

The map links to the decision and publication manifests. Code validates planning operations before projection; the Pages rendering gate
checks the packaged publication. Replay consumes retained
operations without launching providers. See the [decision-process guide](../concepts/decision-process.md).

An optional bus overlay adds `bus-context.geojson` and the map-side loader. It
preserves the planner's decision-map and publication manifests.

## Pages boundary

The Pages workflow consumes a separately prepared `satn-pages.zip`, checks the
packaged maps in Chromium, and deploys only the validated tree. It does not run
compilation or create the archive. The mechanical bundle is for local
inspection; a native-agentic deployment needs the decision-map publication
artifacts. See [Review and publish a native deployment](../guides/publish-a-deployment.md).

## Retained Python artifact schema

The `run.json`, `network.gpkg`, `network-map.pdf`, progressive manifests,
schema-2 Area Deployments, and legacy review-map ZIPs belong to the retained
Python compiler and packaging scripts. See the
[historical implementation reference](../compiler-architecture.md#historical-implementations).

The retained Python compiler's local publication used these artifacts:

| Python artifact | Use |
| --- | --- |
| `review-map/index.html` | Backend-free interactive review map. |
| `network.gpkg` | Multi-layer GIS output. |
| `network.geojson` | Portable network features. |
| `reviewable-network.geojson` | Review surface including non-routable findings where present. |
| `network-map.pdf` | Printable map with title, legend, scale, and disclaimer. |
| `run.json` | Run identity, criteria, status, feature roles, and runtime governance. |
| `agent-records.json` | Bounded-agent request/response provenance; deterministic records are valid. |
| `human-intervention-requests.json` | Structured requests remaining for human action. |
| `divergence-records.json` | Officer/reference/compiler divergence records. |
| `asset-accounting.json` and `.geojson` | Governed asset scope, participation, and disposition. |
| `backbone-comparison.json` | Comparison against a configured reference where permitted. |
| `review-map.zip` | Exact portable local review-map directory. |

Python `scripts/publish_site.py` assembled validated compiler output into
`build/deployments/DEPLOYMENT_ID/` with progressive manifests, indexed shards,
and downloads. `scripts/package_pages.py` assembled those schema-2 deployments
into a catalogue tree and release ZIP. `publication.json` and
`compiler-run.json` recorded deployment and run identity; the current Rust
publication instead uses `decision-map.json`, `decision-map.geojson`, and
`publication.json` under the contract above.
