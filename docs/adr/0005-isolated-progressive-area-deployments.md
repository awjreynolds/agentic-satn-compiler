# ADR 0005: Area Deployments are simple browser-gated POC releases

- Status: accepted (simplified 2026-08-19)
- Date: 2026-08-19

## Context

Each Area Definition must still produce an isolated static Area Deployment so
B&NES, WECA and later regions can be reviewed without overwriting one another.
Large optional evidence still needs progressive loading so the initial map remains
usable within GitHub Pages' hosting limit.

The earlier release design added tracked per-area provenance locks, a catalogue
lock, content hashes for every public file, a four-pass publication sequence and a
second standalone release validator. It made ordinary POC publication depend on
thousands of lines of trust and lineage machinery even though the release is built
from this repository, transported by GitHub and inspected immediately in a real
browser before deployment.

## Decision

An Area Deployment is produced once from a validated compiler publication. The
Pages release then has one small operational flow:

1. build the configured Area Deployments;
2. assemble the catalogue and deployment directories into `satn-pages.zip` while
   enforcing the configured Pages size limit and required-file shape;
3. extract the release in the Pages workflow;
4. open every packaged deployment in Chromium and prove that the complete
   Strategic Network layers are present and visibly rendered; and
5. deploy only when that browser gate passes.

Progressive Evidence Layers remain split into deterministic, stable shard files for
viewport loading. They do not use content-addressed filenames or carry per-file
cryptographic provenance. The browser gate proves that the packaged map can fetch
and display the release it is about to deploy.

Generated Area Deployments and release archives remain process artifacts rather
than Git history. GitHub Pages remains a hosting adapter, not part of SATN network
identity. Compiler validation, network provenance in the ordinary published
records, officer decisions, visible gaps and deterministic SATN feature identities
remain unchanged.

The following former release mechanisms are intentionally removed:

- `provenance-lock.json` and `catalogue-lock.json`;
- bootstrap, lock-generation, rebuild and lock-verification passes;
- whole-release SHA-256 inventories and cyclic-file exceptions;
- the duplicate isolated release validator; and
- repository secret scanning introduced only to suppress public-hash false
  positives.

## Consequences

Publication has one deep packaging interface and one display-oriented acceptance
gate. A copied or damaged archive is not given a custom cryptographic trust model;
it either fails extraction, required-file checks or browser rendering. This is the
appropriate trade-off for an experimental, non-adopted proof of concept.

This decision supersedes the earlier trust-lock, content-addressing and four-pass
publication text in this ADR. It does not remove hashes that implement stable SATN
feature or geometry identity inside the compiler.
