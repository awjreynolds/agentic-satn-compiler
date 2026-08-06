# Package and publish a deployment

Publication is a separate authority boundary after compilation and review. These
steps create reproducible static artifacts; they do not adopt or approve a network.

## One deployment: non-circular provenance

Working directory: repository root. Replace the Area Definition path consistently.

```shell
uv run python scripts/publish_site.py path/to/area.yaml --bootstrap
uv run python scripts/deployment_provenance.py generate path/to/area.yaml --deployment build/deployments/DEPLOYMENT_ID
uv run python scripts/publish_site.py path/to/area.yaml
uv run python scripts/deployment_provenance.py verify path/to/area.yaml --deployment build/deployments/DEPLOYMENT_ID
```

Why there are four steps:

1. the lock-free build determines the exact publication bytes;
2. lock generation fingerprints those bytes;
3. the normal build embeds the tracked lock; and
4. verification proves the final output and lock agree.

Never edit a provenance lock or copy another deployment's lock.

## Catalogue and release package

After every declared deployment has a verified build:

```shell
uv run python scripts/build_deployment_catalogue.py
uv run python scripts/package_pages.py
uv run python -I scripts/validate_pages_release.py build/satn-pages.zip build/validated-pages --catalogue deployments/catalogue.yaml
```

Success: `build/satn-pages.zip` validates within the configured byte budget and the
extracted catalogue contains only declared deployment roots. The Pages workflow uses
the ZIP as temporary release transport and removes that archive after successful
deployment.

## Public versus local evidence

`publication.audience: public` must omit controlled geometry unless redistribution is
explicitly permitted. A local browser may load a governed comparison file for that
session without uploading or republishing it.

## Release gate

Before external publication, verify:

- the Area Definition and deployment catalogue identities agree;
- runtime governance permits the intended publication class;
- every output carries the experimental disclaimer;
- source licences and attribution are present;
- the deployment lock and root catalogue lock pass; and
- the interactive map, PDF and downloads were inspected from the packaged bytes.
