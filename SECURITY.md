# Security Policy

## POC operating model

This is a local proof of concept for building networks, operated by a trusted user
in their own workspace. Run one publisher at a time for each output directory.
Publication uses temporary output, backup and rollback to protect against ordinary
write failures. It checks destination ownership and rejects unsafe destinations,
but does not defend against hostile processes changing filesystem paths during a
publication or coordinate competing writers.

Input validation supports usable networks. Ordinary builds do not perform full
source-code or evidence-file checksum audits. Compiler cache invalidation uses an
explicit revision, which must change when compiler semantics or cached formats
change. Use `--full` for a fresh computation; source edits are not automatically
detected by hashing. See [the POC build contract](docs/adr/0025-local-poc-build-and-cache-contract.md).

## Supported version

Security fixes are made on the latest revision of `main`.

## Reporting a vulnerability

Please use [GitHub private vulnerability reporting](https://github.com/awjreynolds/agentic-satn-compiler/security/advisories/new). Do not disclose a suspected vulnerability in a public issue.

Include the affected code or workflow, the conditions needed to reproduce the issue, and its security impact. Reports and fixes will be coordinated through the private advisory.
