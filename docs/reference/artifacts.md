# Generated artifact reference

Compilation publishes atomically into `publication.output_dir`.

| Artifact | Use |
| --- | --- |
| `review-map/index.html` | Backend-free interactive review. Serve the directory over HTTP for normal browser use. |
| `network.gpkg` | Authoritative multi-layer GIS output. |
| `network.geojson` | Portable published network features. |
| `reviewable-network.geojson` | Complete review surface, including non-routable findings where applicable. |
| `network-map.pdf` | Printable map with title, legend, scale and disclaimer. |
| `run.json` | Run identity, criteria, status, authoritative feature list and runtime governance. |
| `agent-records.json` | Typed bounded-agent request/response provenance. Empty or deterministic-test records are valid. |
| `human-intervention-requests.json` | Structured requests that remain for human action. |
| `divergence-records.json` | Officer/reference/compiler divergence records. |
| `asset-accounting.json` and `.geojson` | Exhaustive governed asset scope, participation and disposition. |
| `backbone-comparison.json` | Structured comparison against a configured reference where permitted. |
| `review-map.zip` | Exact portable local review-map directory. Deployment packaging may omit this duplicate. |

## Deployment artifacts

`scripts/publish_site.py` builds `build/deployments/DEPLOYMENT_ID/` from the already
validated compiler publication. It contains `publication.json`, the compiler run,
progressive layer/topography/evidence manifests, indexed shards and downloads.

`scripts/package_pages.py` assembles the declared deployment roots into a validated
catalogue tree and temporary `build/satn-pages.zip` release transport. Packaging
checks catalogue/deployment identity, required files, WGS84 geometry, progressive
manifest shape and the configured package-size budget. The Pages workflow extracts
the archive and runs the Chromium rendering gate before upload and deployment.
The public Area Deployment keeps one canonical Effective Strategic Network
projection in `data.js` as `reviewable_network`; the compiler-only strategic
sidecar and portable review-map ZIP are deliberately omitted because they duplicate
that runtime projection.

## Reading provenance and timing

`publication.json` is the deployment-level index. Its `run_id`, status, input and
compilation fingerprints, `compilation_metadata.completed_at_utc` and
`compilation_metadata.duration_seconds` identify when compilation finished and
publication began, and how much monotonic compiler time elapsed before that boundary.
`compiler-run.json` carries the criteria, authoritative feature roles and diagnostics.
A changed snapshot, configuration, accepted decision or active compiler dependency
produces a new compilation identity rather than silently reusing an old result.

The interactive deployment opens the Strategic Network and Places layers. Optional
layers include discarded candidates, existing/upgradeable assets, officer divergence,
graph diagnostics, Candidate Low-Traffic Areas, Schools, traffic evidence and
topography. Optional does not mean ungoverned: each layer retains its source,
fingerprints and evidence state, including explicit unknown or unavailable values.

## Stable references

Library callers should refer to artifacts and features through
`PublishedArtifactReference` and `PublishedNetworkFeatureReference`. These references
carry source artifact hashes and stable identifiers without copying geometry into an
untracked decision record.
