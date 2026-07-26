# ADR 0006: Agentic selection is a local, open-source provenance pipeline

## Context

SATN is a local compiler and agentic harness. A person supplies governed input
files or accepts externally supplied sources, runs the compiler on their own
machine, inspects its map and records, and may publish static output through
GitHub Pages. The repository is intended to be open source. It must not contain
hidden trust roots, private signing material, credentials, or a claim that a
locally generated record proves the real-world identity of an agent or person.

Earlier selection-core work treated primary-agent responses, critic responses,
runtime failures, review progression, human adoption, and challenge waivers as
messages from hostile remote principals. That introduced signature verification,
configured key material, signed leases and signed receipt models. This security
model does not fit a local compiler: an operator able to change inputs or code
can also change a local verifier.

## Decision

The compiler trusts its local operator and the governed inputs they choose to
run. It continues to treat model output as structurally untrusted.

- The compiler creates the only finite option menu. Responses must select an
  offered action and bind to the exact request, profile, scenario and evidence
  fingerprints.
- A primary decision and independent critique are separate recorded invocations
  with distinct role and prompt contracts. Independence is an explicit process
  record, not cryptographic proof of two external principals.
- SHA-256 fingerprints provide deterministic tamper/staleness detection and
  replay lineage. They are integrity identifiers, not authentication claims.
- Provider timeouts and rejections are typed local runtime records. Maximum
  rounds and retries are counted within a compile/replay run; a new compiler run
  is allowed and has a new deterministic run record.
- A Reference SATN decision is transparent attributable metadata: decision ID,
  decision-maker name and label, date, rationale, evidence IDs, source URL, and
  exact scenario/profile/evidence/run fingerprints. It records selection only;
  it does not assert funding, delivery, statutory approval, or digital identity.
- Material challenges require revision or remain unresolved. Mandatory Red
  gates are never bypassed. There is no signed waiver subsystem.

Git history, GitHub repository permissions, pull-request review, Actions logs,
and the GitHub Pages deployment provide the relevant boundary for public
publication. A future hosted multi-user decision service may introduce an
explicit remote authentication design at that service boundary; it is outside
this local compiler ADR.

## Consequences

The output remains reproducible, inspectable, stale-safe, and suitable for an
open-source audit. It no longer claims security properties the product cannot
meaningfully provide. Local users retain responsibility for their chosen inputs,
model provider configuration, recorded human decision, and GitHub publication.
