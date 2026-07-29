# ADR 0017: B&NES selects whole RoadLinks from the governed WECA Source Export

- Status: accepted
- Date: 2026-07-29
- Issue: #224
- Related: ADR 0011, ADR 0013, ADR 0016 and issue #200

## Context

The historical B&NES Open Roads artifact,
`data/governed/banes-os-open-roads-2026-04-07.geojson`, contains 10,776
RoadLinks but only `id`, `road_classification` and geometry. Its raw SHA-256 is
`bb39710d078c52366fc0c75205cbd396db6d408f96f43bfb216ba15613d82f5b`.
It was clipped to the B&NES boundary: 136 records have shorter geometry than the
same publisher RoadLink in the retained WECA artifact, including 14 multipart
clipping results.

The governed WECA artifact,
`data/governed/weca-os-open-roads-2026-04-07.geojson`, is from the same
7 April 2026 OS Open Roads national package. Its raw SHA-256 is
`87c944fb4c4f77c949f25913c58b3e7f49df80bbe0bf317606b32feb0653e89c`.
It contains all 10,776 historical B&NES RoadLink IDs, with no classification
differences, and supplies every field required by
`satn-open-roads-ingestion/v1`: `id`, `road_classification`, `road_function`,
`road_classification_number`, `name_1` and line geometry.

Joining those missing attributes onto the clipped B&NES artifact would create a
hybrid source. Writing an ID-filtered copy and presenting its new bytes as a
publisher Source Export would also obscure the retained raw authority described
by ADR 0011.

## Decision

The unchanged governed WECA bytes are the v1 Open Roads Source Export for B&NES.
Its raw fingerprint, release/effective date, licence, declared CRS and national
package provenance remain the source identity.

B&NES is a spatial selection over that Source Export. Snapshot acquisition:

1. reads the governed snapshot boundary;
2. selects source RoadLinks whose geometry intersects that boundary; and
3. retains each complete source RoadLink geometry without clipping it.

The derived snapshot manifest records
`satn-official-road-boundary-selection/v1`, the `intersects` predicate,
`retain-whole-source-feature`, the selected feature count, the WECA Source
Export SHA-256 and the exact snapshot boundary SHA-256. The Local Evidence Store
uses the same source authority and whole-feature semantics through its existing
partition/query contract.

The historical `banes-osm-current` snapshot and clipped governed artifact remain
immutable. A new retained-core snapshot,
`banes-osm-open-roads-v1-2026-07-29`, carries the old OSM core under its exact
manifest SHA-256
`d54cd57ff2b1a92fab0b48a8b616cc4bcb8721b274024880d4ccc17b9a486c39`
and regenerates official-road evidence from the WECA Source Export.

## Reviewed before/after migration

| Property | Historical B&NES | B&NES Open Roads v1 |
| --- | --- | --- |
| Source/export raw SHA-256 | `bb39710d078c52366fc0c75205cbd396db6d408f96f43bfb216ba15613d82f5b` | `87c944fb4c4f77c949f25913c58b3e7f49df80bbe0bf317606b32feb0653e89c` |
| Source scope | B&NES-clipped derived export | Governed WECA export, selected for B&NES |
| Required attributes | classification only | all five v1 attributes |
| Selected RoadLink IDs | 10,776 | 10,776 |
| Classification changes | — | 0 |
| Geometry rule | clip to B&NES boundary | retain whole intersecting RoadLink |
| Changed geometries | — | 136 |
| Geometry types | 10,762 LineString; 14 MultiLineString | 10,776 LineString |
| Total selected length | 1,399,402.057 m | 1,434,826.234 m |

The additional 35,424.177 m consists of the outside-boundary portions of those
same crossing RoadLinks. It is not a new in-area road, inferred geometry or an
OSM substitution.

## Invariants

1. No emitted official-road field or geometry is taken from a second source.
2. Boundary selection filters observations but never clips source geometry.
3. The snapshot `content_fingerprint` remains the exact WECA Source Export
   SHA-256, not the derived snapshot file hash.
4. The historical artifact and snapshot are not overwritten, resealed or
   relabelled as v1.
5. A missing/invalid boundary or an empty selected result fails closed.
6. Any future change to the source export, predicate or geometry treatment
   requires a new snapshot and reviewed migration.

## Rejected alternatives

- **Backfill missing attributes into clipped B&NES geometry.** This is a hybrid
  source with misleading lineage.
- **Treat a new B&NES subset file as received raw authority.** It replaces the
  retained Source Export identity with a local transformation.
- **Load all WECA rows into the B&NES snapshot.** It changes the Area
  Definition's evidence scope and compiler inputs.
- **Clip the v1 result.** It breaks stable whole-feature geometry and makes
  overlapping/disconnected Evidence Coverage less reusable.

## Consequences

The B&NES snapshot oracle changes deliberately and cannot be compared to the old
clipped geometry as an unchanged semantic oracle. Issue #200 must bind its real
B&NES acceptance to the new snapshot ID and require exact feature IDs,
classifications, optional attributes, canonical whole geometry and WECA source
lineage. Existing historical publications remain valid under their recorded
snapshot and source fingerprints.
