# Troubleshooting by pipeline stage

Start from the first stage that did not produce its declared success signal.

## Install

**Symptom:** `uv` cannot create or use its cache.

Check filesystem permissions for the configured cache. In a sandboxed agent
environment, use the product's normal approval mechanism; do not redirect dependencies
into the repository or edit the lock file.

**Symptom:** Python version is rejected.

Install Python 3.12+ through `uv python install 3.12`, then rerun `uv sync --frozen
--all-groups`.

## Snapshot

**Symptom:** a configured local evidence file is missing.

Determine whether it is committed, a governed download, controlled local evidence or
optional context. Follow its acquisition contract. Do not create an empty placeholder
or remove the field merely to satisfy validation.

**Symptom:** target snapshot exists but fails validation.

Treat it as immutable evidence corruption or configuration mismatch. Preserve it for
diagnosis and use a new explicit snapshot ID for reacquisition; do not patch members.

## Compile

**Symptom:** output says `reviewable` or reports gaps.

This can be a successful compile. Inspect Network Gaps, Evidence Requests, asset
non-participation and criteria. A Reviewable Network is the honest result when the
compiler cannot evidence continuous service.

**Symptom:** the agent runtime is unavailable or changes behaviour.

The finite decision must use its configured deterministic fallback. Verify request and
dependency fingerprints. Never accept free text, new geometry or an unoffered choice
to make the run pass.

**Symptom:** an existing cycleway/PROW is absent from the selected network.

Enable Existing/Upgradeable Assets and Unselected Candidates. Inspect Asset Accounting
for scope, candidate participation, evidence state, topology and disposition. Absence
from selection must not mean absence from accounting.

## Review map

**Symptom:** opening `index.html` directly does not load optional data.

Serve the directory:

```shell
uv run python -m http.server 8000 --directory path/to/review-map
```

**Symptom:** the map looks noisy.

Return to the default strategic network and Places view, then add one optional evidence
layer at a time. Whole-region optional loading can transfer large data volumes.

## Packaging and Pages

**Symptom:** packaging rejects the deployment.

Read the first required-file, catalogue identity, WGS84, progressive-manifest or
package-size error. Packaging is intentionally fail-closed; do not weaken it to
publish partial bytes.

**Symptom:** the Pages workflow rejects a release after extraction.

Inspect the Chromium gate output for the first map that failed to load or render.
The archive is uploaded only after every packaged review map shows the complete
strategic network.

## Report a defect

Include the Area Definition path, run/snapshot ID, exact command, first failing stage,
actual output and the smallest relevant artifact fingerprint. Do not attach controlled
or personal evidence to a public issue.
