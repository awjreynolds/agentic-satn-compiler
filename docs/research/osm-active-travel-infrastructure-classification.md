# OSM active-travel infrastructure classification

Research for [Identify OSM classifications that declare active-travel infrastructure](https://github.com/awjreynolds/agentic-satn-compiler/issues/395).

- Retrieved: 2026-08-19
- Scope: explicit OSM way/relation classifications that should enter SATN asset accounting automatically
- Sources: current OSM Wiki documentation and the pinned B&NES/WECA routing snapshots

## Decision in brief

OSM's `highway=*` value is not the whole classification. The SATN should admit an **OSM Active Travel Asset** whenever the mapped way explicitly describes a cycling facility, an active-travel-only/shared way, or a signed bicycle route. A permission-only tag should still create a visible access-enabled opportunity, but must not be confused with a designated facility. Admission is not a claim that the asset is good, legally protected, continuous, or strategically suitable. Those are separate observations and officer-review controls.

This would have admitted Norton Radstock Greenway: its pinned OSM geometry is named `Norton Radstock Greenway` and tagged `highway=cycleway`, even though it is not an NCN route.

## OSM semantics relevant to admission

The [OSM `highway=cycleway` page](https://wiki.openstreetmap.org/wiki/Tag%3Ahighway%3Dcycleway) describes a designated cycleway and implies `bicycle=designated`. The [cycleway key](https://wiki.openstreetmap.org/wiki/Key%3Acycleway) describes cycling infrastructure carried by another highway, including `lane`, `track`, `shared_lane`, `share_busway`, `shoulder`, `link` and `crossing`; `no` explicitly records that no cycling infrastructure was found. Side-specific `cycleway:left`, `cycleway:right` and `cycleway:both` variants describe the same infrastructure on a particular side of a road. `cycleway=separate` is a pointer that the parallel facility is mapped as a separate way, not a second facility.

The [OSM `highway=path` page](https://wiki.openstreetmap.org/wiki/Tag%3Ahighway%3Dpath) intentionally describes a generic path. The [footway](https://wiki.openstreetmap.org/wiki/Tag%3Ahighway%3Dfootway) and [pedestrian](https://wiki.openstreetmap.org/wiki/Tag%3Ahighway%3Dpedestrian) pages describe pedestrian-oriented ways. The [bridleway page](https://wiki.openstreetmap.org/wiki/Tag%3Ahighway%3Dbridleway) records an equestrian way and documents implied `bicycle=yes`; it is therefore a multi-use corridor candidate, not evidence of a designed cycle facility. A [track](https://wiki.openstreetmap.org/wiki/Tag%3Ahighway%3Dtrack) is a minor land-access road, so a bare track is not an active-travel asset.

`bicycle=designated` and `bicycle=yes` explicitly describe permitted/designated cycling; `bicycle=permissive` describes permission that may be withdrawn. `bicycle=use_sidepath` is a routing/legal instruction, not the sidepath geometry itself. The [OSM bicycle key](https://wiki.openstreetmap.org/wiki/Key%3Abicycle) and [designation page](https://wiki.openstreetmap.org/wiki/Designation) support keeping these access/legal observations separate from the physical asset. There is no documented OSM `shared_use` key in the current Wiki; represent shared use with the normal combination, commonly `highway=path` + `bicycle=designated` + `foot=designated` + `segregated=*`.

[`bicycle_road=yes`](https://wiki.openstreetmap.org/wiki/Key%3Abicycle_road) and [`cyclestreet=yes`](https://wiki.openstreetmap.org/wiki/Key%3Acyclestreet) describe a bicycle-priority road. They should enter the asset inventory, while remaining a road corridor rather than being mistaken for a separate cycleway.

A [`type=route` + `route=bicycle` relation](https://wiki.openstreetmap.org/wiki/Tag%3Aroute%3Dbicycle) may use roads, paths or dedicated cycleways. Its `network=lcn`, `rcn`, `ncn` or `icn` value identifies the route network. The [cycle-routes guidance](https://wiki.openstreetmap.org/wiki/Cycle_routes) says old per-way `lcn=*`, `rcn=*` and `ncn=*` tags are deprecated; accept them only as compatibility signals and prefer relation membership.

[`railway=disused`](https://wiki.openstreetmap.org/wiki/Tag%3Arailway%3Ddisused) and [`railway=abandoned`](https://wiki.openstreetmap.org/wiki/Tag%3Arailway%3Dabandoned) describe railway lifecycle/former-alignment evidence, not current public cycling provision. They should remain visible as former-railway opportunities. They become current assets only when the same geometry (or a linked parallel way) also has a current path/cycleway classification or explicit bicycle access.

## Recommended admission matrix

“Admit” means create an asset/evidence record. It does not set `existing-provision`; condition, access, continuity, ownership, safety and strategic selection remain independently assessed.

| OSM signal | Admit automatically? | Initial asset interpretation |
| --- | --- | --- |
| `highway=cycleway` | Yes | Dedicated cycle facility; preserve all raw tags. |
| `cycleway=*` on a road, including `left/right/both` | Yes when value is not `no` or `separate` | Cycle lane/track/shared lane attached to the road; `separate` links to the separately mapped way. |
| `bicycle_road=yes` or `cyclestreet=yes` | Yes | Bicycle-priority road corridor. |
| `highway=path` + `bicycle=designated/yes` | Yes | Cycle-capable path; shared-use details are separate. |
| `highway=path` + `foot=designated` + `bicycle=designated/yes` | Yes | Shared-use path. |
| `highway=footway` or `pedestrian` + `bicycle=designated/yes` | Yes | Pedestrian-led way with explicit cycling designation/access; retain the mode qualifiers. |
| `highway=path/footway/pedestrian/track` + `bicycle=permissive` | Yes, as an access-enabled opportunity | Mapped cycling permission, not a designated cycle facility; mark access as revocable/provisional. |
| `highway=bridleway` | Yes | Multi-use/bridleway corridor; OSM implies bicycle access, but do not call it a cycle track. |
| `highway=track` + explicit bicycle access or `route=bicycle` membership | Yes | Cycle-compatible land-access corridor; suitability and public access remain unknown where not stated. |
| Any `type=route` + `route=bicycle` relation (`lcn`/`rcn`/`ncn`/`icn`) | Yes, by joining member ways | Signed bicycle-route evidence; it may run on an ordinary road. |
| `highway=service/residential/unclassified` with no explicit cycle signal | No as a cycle asset | Keep as routable street fabric; it can still be selected as a low-traffic connection by normal SATN logic. |
| Bare `highway=path`, `footway`, `pedestrian` or `track` | No as a cycling asset; retain for walking/provisional opportunity accounting | Mode and suitability are not explicit. |
| `railway=disused/abandoned` without a current path/cycle tag | No current asset; yes former-railway opportunity | Former alignment only. |
| `highway=proposed`/`construction` or `cycleway=proposed` | Admit as planned/under-construction evidence, not existing provision | Preserve for officer review and future-state planning. |

`bicycle=permissive`, `access=permissive`, `access=unknown`, `bicycle=no` and contradictory combinations must never be discarded. They change the access/constraint state; they do not erase the mapped geometry. `bicycle=no` excludes that way from a cycling candidate unless a separately mapped parallel facility is present.

## Pinned snapshot measurements

The pinned routing exports contain `highway`, name, access and routing attributes but do **not** preserve `cycleway:*`, `bicycle`, `designation`, `route` relation membership or `railway` tags. Therefore the counts below are a lower-level geometry inventory, not proof that the absent keys have zero values.

Command used (scalar values only):

```sh
jq '[.features[].properties.highway | select(. == "cycleway" or . == "path" or . == "footway" or . == "pedestrian" or . == "bridleway" or . == "track")] | group_by(.) | map({highway: .[0], edges: length})' <snapshot>/network.geojson
```

| Pinned snapshot | Edges | cycleway | path | footway | pedestrian | bridleway | track |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `banes-osm-current` | 41,158 | 609 | 162 | 0 | 138 | 136 | 1,438 |
| `weca-classification-elevation-2026-07-31-v14-fp-20260731T092920522968Z-02-fp-20260809T123804416841Z-01` | 256,327 | 6,987 | 4,056 | 0 | 459 | 744 | 5,710 |

The exports also encode some OSM ways with multi-valued `highway` arrays; counting features containing a value (rather than only scalar values) gives cycleway/path/footway/pedestrian/bridleway/track counts of `667/176/0/152/176/1,654` for B&NES and `7,631/4,492/0/555/944/6,476` for WECA. These are routing-edge counts, so bidirectional edges can represent one physical way more than once.

The B&NES snapshot contains 16 named `Norton Radstock Greenway` routing edges, each with `highway=cycleway`; this is the concrete regression case for non-NCN admission.

## Implementation consequences

1. Preserve raw OSM tags and relation membership in the source adapter; the current routing export is insufficient for this admission matrix.
2. Emit an asset for every admitted signal, including low-quality, permissive or incomplete cases, with explicit `intervention_state`/constraint unknowns.
3. Let the existing selection profile and officer controls decide strategic use, upgrade versus retain, and exclusions. Do not make NCN membership a prerequisite for asset visibility.
4. Add fixtures for Norton Radstock and one example each of side-specific `cycleway=*`, shared `path`, bridleway, bicycle-priority road, bicycle route relation and former railway with/without a current path tag.
