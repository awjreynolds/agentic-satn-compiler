# Greenway candidate coverage evidence

This note supports [Determine why planning alternatives omit the Norton–Radstock Greenway](https://github.com/awjreynolds/agentic-satn-compiler/issues/492). It explains the retained Radstock–Midsomer Norton town-pair expansion's four alternatives. It does not claim current usability, access, safety, route quality, or a routing defect.

## Finding

The four alternatives are endpoint-generated, role-specific paths. Their zero
`ncn_share` and lack of an exact Greenway intersection have three separate
causes:

1. The named Greenway edge is physically reachable, but the overlapping
   context records are `ncn-link` connector links, not `ncn-route`,
   `declassified-ncn-route`, or `greenway-cycleway` records. The strategic
   enrichment set excludes `ncn-link`; it is used for public-cycle-route
   classification, so those rows do not set `ncn=True` or add a strategic
   alignment basis.
2. The existing endpoint paths are cheaper under each current role's weight
   function, so `choose_alignment(..., strategic_use=True)` retains those
   options.
3. Source inventory admission is separate from town-pair expansion. The
   inventory retains the Greenway source corridors; `expand_connection` does
   not enumerate every admitted corridor between two places when its request
   carries no corridor refs.

The inventory contains 2,999 admitted source corridors, 83
`greenway-cycleway` sections, and 472 unique attached directed edge IDs. None
of the four endpoint paths intersects those 472 IDs. It separately retains
three attached `cycleway` corridors named `Norton Radstock Greenway`, with
source IDs `1361731394`, `26624188,261241204`, and `407663143`.

The seven context rows overlapping named Greenway geometry are source IDs
`42133.0`, `38642.0`, `36997.0`, `18634.0`, `18633.0`, `40529.0`, and
`18632.0`; all are `ncn-link` with `ncn_evidence_role=connector-link`. The
named Greenway contributes a reachable `highway=cycleway` edge with
`source_edge_id=1361731394`, `directed_edge_id=1361731394#641764c8cc1429e814bc`,
`ncn=false`, and no `cycle_alignment_bases`.

## Existing endpoint options and forced-edge measurement

The compact values below are from the frozen `costs.json`; weighted cost is
the existing role function, not a new score or quality judgment.

| role | length (m) | weighted cost | `ncn_share` | A-road share | forced-edge cost |
| --- | ---: | ---: | ---: | ---: | ---: |
| direct | 2524.7366 | 2524.7366 | 0.0 | 0.6784 | 2726.1426 |
| strategic-spine | 2613.9797 | 1396.6545 | 0.0 | 0.8526 | 1454.8504 |
| ncn-informed | 2524.7366 | 3282.1575 | 0.0 | 0.6784 | 3543.9853 |
| low-traffic | 2853.3572 | 2546.5774 | 0.0 | 0.0438 | 2569.3040 |

The selected existing option is `strategic-spine`, with reason `A-road
Strategic Spine selected for directness and social oversight.` The forced-edge
column is a targeted comparison: for each role it is the minimum weighted-cost
path through the single directed Greenway edge
`1361731394#641764c8cc1429e814bc`, between endpoint nodes `1545936215` and
`4664155942`. It is not a whole-Greenway enumeration or a claim that this
forced path is the role's selected alignment. Its shortest physical route
length is 2.7261426 km.

## Why this is not an automatic candidate omission defect

`build_planning_problem` admits configured source corridors and can generate
source-bound candidates for each corridor's own geometry endpoints. The
retained `expand_connection` call instead attaches the two named places and
asks `choose_alignment` for that endpoint pair. Its candidates have endpoint
provenance and no `source_corridor_refs`; it is not required by the current
brief to enumerate every mapped cycleway or connector-link as a town-pair
alignment.

The existing `admit_expansion(problem, state, request)` seam can reuse a
deliberately prepared source-bound candidate. Such a request must bind the
current problem/state/brief/snapshot, named places, admitted
`source_corridor_ref`, contiguous directed and source edge IDs, endpoint
attachments, graph evidence, and matching geometry fingerprint. It preserves
the bound current/future value and source provenance; it does not infer either
from the Greenway name. This reuses the existing admission API rather than
adding weights or a new framework.

The current brief requires source inventory retention, explicit unknowns and
departure outcomes. It does not require automatic enumeration of all source
corridors into every town-pair expansion. A deliberately prepared candidate
may be compared autonomously in the next [Admit and replay a source-constrained Greenway alternative](https://github.com/awjreynolds/agentic-satn-compiler/issues/494), under the user's experiment authorization and the existing admission seam. Changing the general coverage rule to require automatic enumeration would be a separate policy change; that policy change is not required to run this bounded comparison.

## Frozen evidence and reproduction

Diagnosis: `/tmp/satn-greenway-coverage-diagnosis.md`.

Frozen artifacts:

Retained copies of the diagnosis, scripts, JSON and checksum file are under
`/Users/awjre/Work/banes-satn/build/typesafe-experiments/2026-09-20-greenway-candidate-coverage/`.
The commands below preserve the exact executed `/tmp` paths; those execution
paths and the retained copies are distinct.

| artifact | SHA-256 |
| --- | --- |
| `costs.py` | `18f06124f27495a20db4fde039faf4772dfc4a2012a60009f88145aaf4e1ca0f` |
| `costs.json` | `e3a144075cde839e76b4adb2643344178c94810a73ad3391fee36887e3256560` |
| `forced_paths.py` | `b18de95c09b55bc68dc7e4be895df44e80eb2ede45ba64d54e90bcfb047b405e` |
| `forced-paths.json` | `3aa4dbe63edc217cb51c1c786f3a5ccc628ad2947727601843957026ba4963c0` |

The exact commands were:

```sh
PYTHONPATH=/private/tmp/satn-greenway-candidate-coverage/src \
  /Users/awjre/Work/banes-satn/.venv/bin/python -u \
  /tmp/satn-greenway-coverage-frozen/costs.py

PYTHONPATH=/private/tmp/satn-greenway-candidate-coverage/src \
  /Users/awjre/Work/banes-satn/.venv/bin/python -u \
  /tmp/satn-greenway-coverage-frozen/forced_paths.py
```

These artifacts are measurements against the pinned snapshot and existing
role functions. They do not establish current provision or justify an
automatic route choice.
