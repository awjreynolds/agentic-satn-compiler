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

`scripts/publish_site.py` builds `build/deployments/DEPLOYMENT_ID/` with a
`publication.json`, compiler run, progressive layer/topography/evidence manifests,
content-addressed shards, downloads and a copied provenance lock.

`scripts/package_pages.py` builds a validated catalogue tree and temporary
`build/satn-pages.zip` release transport. The release validator checks catalogue
identity, allowed roots, artifact hashes, manifests, publication status and package
size before Pages can deploy it.

## Stable references

Library callers should refer to artifacts and features through
`PublishedArtifactReference` and `PublishedNetworkFeatureReference`. These references
carry source artifact hashes and stable identifiers without copying geometry into an
untracked decision record.
