# ADR 0010: Configuration-sensitive compilation dependencies

## Status

Accepted.

## Context

The compiler previously hashed one repository-wide list of 34 SATN modules and
13 runtime distributions. A change to an optional adapter could therefore
invalidate a B&NES publication even when its Area Definition could not execute
that adapter. The measured B&NES cold compile associated with this work was
about 277.9 seconds, while resolving and hashing its dependency manifest takes
about 8.6 ms. Avoiding one unrelated invalidation is therefore material; this
change is not presented as an optimisation of the compile hot path itself.

## Decision

Every controlled installed-package file remains classified exactly once as a
compiler component or a non-compiler exclusion. The registry still fails closed
for a missing, overlapping, symlinked or unclassified file.

Compiler components are divided into a conservative core and small optional
bundles:

- ATM comparison;
- elevation source validation;
- Network Selection and its governed evidence loaders;
- strategic Reference replay;
- direct agent runtime distributions; and
- OSM runtime distributions.

The Area Definition and entry-point path (`network`, `reference` or
`strategic-reference`) select bundles. The manifest records its path, active
groups, resolved component paths, digests, installed versions and inactive
registered components. Its SHA-256 covers the selection and every selected
record. Publication reuse compares the complete recorded manifest, so a legacy
or differently selected manifest recompiles.

B&NES selects 35 of the 47 registered code/runtime components: core, elevation
and OSM bundles. Its configured fake agent does not activate external agent
runtime distributions. Inactive Network Selection and strategic Reference
modules remain audited but do not enter its digest. A fixture with a Network
Selection Profile activates those validators, and the strategic Reference entry
point additionally activates replay code.

## Safely extending dependency sets

When adding a compiler adapter:

1. Register every new package file; an unregistered file must continue to fail
   the manifest build.
2. Put code used on every compilation in the core set.
3. Add an optional bundle only when an Area Definition field or explicit
   compiler entry point proves the branch cannot execute otherwise.
4. Add paired tests: changing the adapter must leave an inactive B&NES/fixture
   digest unchanged and must change the digest when the adapter is active.
5. Include any runtime distribution whose version can affect that active
   branch.
6. Never use optional selection to bypass current publication validation;
   validation still runs before reuse.

If a dependency cannot be guarded confidently, it stays core.

## Consequences

Changes to core routing, schemas, policy and configured adapters always
invalidate. Changes confined to a registered inactive adapter do not. Existing
v2 manifests intentionally miss the v3 selection record and therefore cause
one safe recompilation before they can be reused.
