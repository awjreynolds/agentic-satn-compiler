# Package and publish a deployment

Publication is a separate authority boundary after compilation and review. These
steps create reproducible static artifacts; they do not adopt or approve a network.

## Build one Area Deployment

Working directory: repository root. Replace the Area Definition path consistently.

```shell
uv run satn snapshot path/to/area.yaml
uv run satn compile path/to/area.yaml --full
uv run python scripts/publish_site.py path/to/area.yaml
```

Compilation validates the authoritative output and replaces it atomically. The Area
Deployment command then copies that validated output into the ignored deployment
bundle used by the catalogue and release package.

## Catalogue and release package

After each declared deployment has a validated Area Deployment:

```shell
uv run python scripts/build_deployment_catalogue.py
uv run python scripts/package_pages.py
```

`package_pages.py` assembles the declared deployment roots, validates required files,
progressive manifests, WGS84 geometry and the configured hosting-size budget, then
writes `build/satn-pages.zip`. It also generates the catalogue root, so the separate
catalogue command is only useful when inspecting that root locally.

When the release is published, GitHub Pages downloads and extracts that archive,
installs Chromium, and runs `scripts/validate_pages_rendering.py pages`. The gate
opens every packaged review map, verifies that the complete strategic-spine,
access-connection and cross-spine layers are visible, and proves that strategic-spine
geometry produces rendered map features. Where governed urban main-road spines are
present, it also rejects a package unless they are represented in the Effective
Strategic Network before the validated tree is uploaded and deployed.

## Public versus local evidence

`publication.audience: public` must omit controlled geometry unless redistribution is
explicitly permitted. A local browser may load a governed comparison file for that
session without uploading or republishing it.

## Release gate

Before external publication, verify:

- the Area Definition and deployment catalogue identities agree;
- every output carries the experimental disclaimer;
- source licences and attribution are present;
- every packaged interactive map passes the automated strategic-network rendering
  check before Pages upload; and
- the PDF and downloads were inspected from the packaged bytes.
