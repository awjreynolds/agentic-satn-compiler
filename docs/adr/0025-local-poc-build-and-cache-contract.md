# Local POC builds use explicit cache revisions

- Status: accepted
- Date: 2026-09-05
- Supersedes: ADR 0010's source/dependency checksum inventory; the per-build byte-verification requirements in ADRs 0019 and 0020

The owner clarified that this is a fast, trusted local tool for building and
reviewing networks. Repeated source-code inventories, dependency checksums and
evidence-byte attestations add complexity without serving that workflow.

Compiler implementation identity is an explicit cache revision. Change the
revision when compiler semantics or cached formats change; use `--full` when a
fresh computation is needed. Source edits and installed dependency changes are
not automatically detected through checksums. Existing caches with the old
identity format must rebuild rather than masquerade as current entries.

Ordinary compilation reads and parses local evidence without running a full
store or file-checksum audit. Required files, usable geometry, input structure,
and the consistency needed to build a network still matter. Explicit evidence
verification remains a separate operation. Stable feature identifiers and
input-derived cache keys are not evidence-authentication claims.

Publication retains staged output and ordinary rollback. It does not claim
cryptographic integrity or protection against hostile concurrent writers.

Terrain coverage is assessed against each route's geometry. A change to the
whole network's eligible-route fingerprint does not block publication: routes
covered by the retained elevation samples keep their measured profiles, while
uncovered routes remain explicitly unavailable. Reacquiring terrain evidence
can improve that coverage without becoming a prerequisite for reviewing a new
strategic network. A retained profile must not be presented as evidence for a
different, uncovered alignment.
