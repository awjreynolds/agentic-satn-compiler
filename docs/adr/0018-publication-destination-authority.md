# ADR 0018: Publication destinations require compiler-held authority

- Status: Accepted
- Date: 2026-08-01
- Issue: #266

## Context

An Area Definition is governed input, but it is still untrusted at the
filesystem boundary.  Letting it nominate an arbitrary writable output path
would let a normal non-interactive compilation replace an unrelated directory.
Basic lexical checks are not enough: symlink substitutions and a changed
destination between validation and rename can redirect an otherwise valid
publication commit.

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

Publisher and Area Deployment builders both stage under a no-follow parent
directory descriptor.  Immediately before the two renames they revalidate the
staging inode and current destination authorisation through that descriptor,
then retain the previous directory until the new install succeeds.  A failed
install restores the previous output; a failed validation leaves it untouched.
If a competing process prevents restoration, the error names the retained
previous-publication sibling instead of suppressing the rollback failure or
deleting either output.

## Consequences

- Existing governed deployments remain non-interactive when their outputs are
  under their caller-owned workspace and subsequently carry their marker.
- Operators can automate a deliberately external deployment by supplying the
  exact capability and, where needed, expected prior-run fingerprint.
- Filesystem, home, repository-root, symlinked and swapped destinations fail
  closed rather than being treated as publication targets.
- Area Deployments cannot replace each other merely because their files have a
  superficially valid publication record; owner identity is deployment-specific.
