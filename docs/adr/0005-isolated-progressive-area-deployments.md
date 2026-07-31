# Area Deployments are isolated and publish evidence progressively

An Area Definition for one council, several councils or another coherent region
produces one independently reproducible Area Deployment. Generated GeoJSON,
GeoPackages, PDFs, ZIPs, evidence shards and site bundles are process artifacts and
do not belong in Git history; the repository retains governed definitions, compiler
code and compact deployment manifests. A lightweight Deployment Catalogue links
stable Area Deployment paths, so B&NES, WECA and later regions coexist without
overwriting or depending on one another.

Large optional evidence is published as content-addressed, zoom-dependent spatial
shards behind a small layer manifest. The initial Inspectable Review Map contains
only the strategic regional picture and named constituent-authority boundaries.
Selecting a layer loads its overview and active-view shards in parallel, reports
size and progress, and reuses best-effort browser caching. This was chosen over one
monolithic GeoJSON download, council-specific network forks, and committing generated
sites because the current B&NES site is already 156 MB and GitHub Pages limits one
published site to 1 GB.

Each deployment also has a compact, tracked `provenance-lock.json` beside its Area
Definition. It binds the exact definition and snapshot digests, governed and
accepted-decision inputs, run identity/status, counts, criteria and **every file in
the built public deployment**: the core-subset network, audit JSON, manifests and
shards, HTML/CSS/JS/MapLibre runtime, service worker and PDF. This is intentionally
a manifest rather than spatial data: the builder and standalone release validator can
reject a self-consistent but forged ZIP while the large compiler outputs remain
reproducible process artefacts.

Lock creation is deliberately a controlled two-pass operation, because the public
core network is not byte-identical to the compiler's full network and a manifest
cannot hash itself. First build the deterministic lock-free deployment, then create
the tracked lock from that exact directory, then rebuild normally and verify it:

Compilation identity uses the Area Definition directory as its canonical path
root. Every configured path and governed-file digest key is serialized as a
POSIX relative path from that root; snapshot identity is additionally bound by
the snapshot ID and manifest digest. Absolute checkout prefixes are operational
locations and never enter the governed fingerprint. Moving a governed input
changes its relative identity, and changing its bytes changes its digest, so
both remain fail-closed. This normalization changes fingerprints created by
earlier versions: existing compiler outputs and provenance locks must be
regenerated once, using the controlled sequence below, rather than edited or
accepted as equivalent.

```sh
.venv/bin/python scripts/publish_site.py deployments/weca/area.yaml --bootstrap
.venv/bin/python scripts/deployment_provenance.py generate deployments/weca/area.yaml \
  --deployment build/deployments/weca
.venv/bin/python scripts/publish_site.py deployments/weca/area.yaml
.venv/bin/python scripts/deployment_provenance.py verify deployments/weca/area.yaml \
  --deployment build/deployments/weca
```

When adding a future deployment, repeat that four-step sequence for *each*
affected Area Definition: bootstrap the lock-free output, generate its tracked
`provenance-lock.json`, perform the final normal build, and verify that build
against the lock. Do not treat an existing deployment's lock as evidence for a
new area.

After all final Area Deployments have been verified, assemble the Pages release:

```sh
.venv/bin/python -c "from satn.deployment_catalogue import generate_catalogue_lock; generate_catalogue_lock('deployments/catalogue.yaml')"
.venv/bin/python scripts/package_pages.py
```

The explicit first command regenerates the tracked root
`deployments/catalogue-lock.json`. It hashes the deterministic root
`index.html` and `catalogue.json`, and lists the allowed deployment roots. The
packager and per-area `provenance-lock.json` files jointly verify the complete
Pages tree: the root lock does not itself hash generated deployment artefacts.
Review and commit that root lock, every changed per-area `provenance-lock.json`,
and the corresponding tracked Area Definitions/Catalogue together; the generated
`build/` deployment and release files remain process artefacts. Packaging rejects
a missing or stale root lock; it never refreshes it implicitly. Before publishing,
independently extract and validate the release archive rather than trusting the
packager's in-process result:

```sh
.venv/bin/python -I scripts/validate_pages_release.py build/satn-pages.zip \
  build/validated-pages --catalogue deployments/catalogue.yaml
```

This standalone release gate validates the complete tracked file set, content hashes,
provenance, identities and archive safety. Runtime-governance metadata,
decision-ledger provenance and urban-road evidence remain content-bound and
inspectable, but their review status does not block publication of this public proof
of concept. The legacy review option remains accepted for workflow compatibility:

```sh
.venv/bin/python -I scripts/validate_pages_release.py build/satn-pages.zip \
  build/validated-pages --catalogue deployments/catalogue.yaml \
  --allow-non-production
```

The only cyclic deployment files are `provenance-lock.json` itself and the local
`review-map.zip`. The Pages adapter publishes neither the nested ZIP nor a link to it;
the release transport archive is deleted from GitHub after a successful deployment.

Portable PDFs and local Review Map ZIPs remain first-class Area Deployment artifacts.
GitHub Pages is an initial hosting adapter, not part of the Area Deployment identity;
publication must fail its configured size budget before a hosting limit is exceeded.
