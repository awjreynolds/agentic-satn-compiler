# ADR 0018: Publication destinations require compiler-held authority

- Status: Accepted
- Date: 2026-08-01
- Revised: 2026-09-05 — local single-writer POC scope
- Issue: #266

## Context

An Area Definition is governed input, but it is still untrusted at the
filesystem boundary.  Letting it nominate an arbitrary writable output path
would let a normal non-interactive compilation replace an unrelated directory.
The POC is operated by a trusted local user with one publisher per output
directory. Protecting against ordinary mistakes and failed writes is necessary;
defending against hostile processes swapping paths during publication is outside
that operating model.

## Decision

The compiler derives a publication workspace from the caller-owned definition
location, rather than reading an output root from the Area Definition.  A
relative Area Definition destination is usable only beneath that workspace.
The Area Deployment builder uses the same definition-derived default; it never
turns a requested destination's parent into authority.  The normal repository
`build/deployments/` location remains within the repository workspace.
An external destination requires an explicit, non-interactive caller capability
that names that exact destination; it is neither serialised in nor inferred
from governed input.

Replacement is separately authorised.  A compiler may replace only a directory
carrying its exact owner marker (including the Area Deployment identity), or a
directory whose prior `compilation_input_fingerprint` exactly matches a
caller-supplied expected fingerprint.  This permits controlled recovery of a
pre-marker output without turning a matching filename into authority.

Publisher and Area Deployment builders validate the destination before creating
a temporary sibling directory. They retain the previous directory as a backup
until installation succeeds. A failed install restores the previous output; a
failed validation leaves it untouched. If rollback fails, the backup remains and
the error names it. Descriptor-relative operations, inode and content surveillance,
and competing-writer quarantine were removed following the owner's clarification
that this is a fast local network-building POC.

## Consequences

- Existing governed deployments remain non-interactive when their outputs are
  under their caller-owned workspace and subsequently carry their marker.
- Operators can automate a deliberately external deployment by supplying the
  exact capability and, where needed, expected prior-run fingerprint.
- Filesystem, home, workspace-root and symlinked destinations are rejected during
  destination validation. Concurrent path substitution is not supported.
- Area Deployments cannot replace each other merely because their files have a
  superficially valid publication record; owner identity is deployment-specific.
