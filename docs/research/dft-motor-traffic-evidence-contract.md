# DfT motor-traffic evidence contract

**Research status:** implementation-ready recommendation, researched 2026-08-02.

**Question resolved:** how should the compiler use Department for Transport (DfT)
road-traffic evidence when matching traffic observations to candidate active-travel
segments, while preserving provenance and ensuring that missing, stale or
conflicting data never prevents compilation?

## Decision recommendation

Use DfT traffic as a governed, claim-specific evidence layer. Treat an AADF as a
traffic-flow observation about a DfT count-point link, not as a design standard or
an automatic route veto. Keep the raw observation, its Source Export, match proof,
quality state and any conflicts in the evidence record. Derive a configurable
traffic band and selection diagnostic from that record; never replace the record
with a score or a single globally preferred dataset.

The compiler must read only pinned local Source Exports during compilation. An AI
provider may inspect the evidence IDs supplied in a bounded request, but it must
not fetch DfT URLs, discover new raw facts, reinterpret missing values as zero, or
change thresholds. A missing, stale, unreadable or conflicting export produces an
explicit `unknown`/diagnostic result and compilation continues.

## Primary sources checked

All sources below are first-party DfT/GOV.UK material. URLs and retrieval date are
recorded so the next governed refresh can be compared with this research.

| Source | Exact URL | Retrieved | Relevant fact |
| --- | --- | --- | --- |
| DfT API documentation | <https://roadtraffic.dft.gov.uk/api-documentation> | 2026-08-02 (page says updated 2026-06-19) | Unauthenticated REST API; endpoint schemas and filters for count points, AADF, AADF-by-direction and raw counts. |
| DfT downloads and guidance | <https://roadtraffic.dft.gov.uk/downloads> | 2026-08-02 | Count-point CSVs cover 2000 onward; `estimation_method` is a quality flag; individual link estimates can be less robust or not current; OGL v3.0 applies. |
| DfT traffic metadata PDF | <https://storage.googleapis.com/dft-statistics/road-traffic/all-traffic-data-metadata.pdf> | 2026-08-02 | Variable definitions, AADF/raw-count semantics, count-point link model, minor-road sampling, directions, road categories and vehicle classes. |
| DfT count-points map | <https://roadtraffic.dft.gov.uk/count-points> | 2026-08-02 | AADF is produced for each major-road link and a sample of minor-road locations; location table exposes CP, year, road and junction names. |
| DfT FAQ | <https://roadtraffic.dft.gov.uk/frequently-asked-questions> | 2026-08-02 | Every major link has a CP; minor roads are a representative sample refreshed every 10 years, so many minor roads have no recent or prior sample. |
| GOV.UK background quality report (2024) | <https://www.gov.uk/government/statistics/road-traffic-estimates-in-great-britain-2024/background-quality-report> | 2026-08-02 | AADF methods are counted, grown, ATC, dependent and derived; counted and estimated semantics and limitations. |
| GOV.UK notes and definitions (2024) | <https://www.gov.uk/government/statistics/road-traffic-estimates-in-great-britain-2024/notes-and-definitions> | 2026-08-02 | Major-road AADF/link calculation and distinction between major and minor estimates. |
| GOV.UK traffic statistics information | <https://www.gov.uk/guidance/road-traffic-statistics-information> | 2026-08-02 | Canonical methodology collection and minor-road review entry point. |

The representative requests below were made against the API on 2026-08-02:

```text
GET https://roadtraffic.dft.gov.uk/api/count-points?page[size]=1&page[number]=1
GET https://roadtraffic.dft.gov.uk/api/average-annual-daily-flow?page[size]=1&page[number]=1
GET https://roadtraffic.dft.gov.uk/api/average-annual-daily-flow-by-direction?page[size]=1&page[number]=1
GET https://roadtraffic.dft.gov.uk/api/raw-counts?page[size]=1&page[number]=1
```

The 2025 download links currently exposed by the downloads page are:

```text
https://storage.googleapis.com/dft-statistics/road-traffic/downloads/data-gov-uk/count_points.zip
https://storage.googleapis.com/dft-statistics/road-traffic/downloads/data-gov-uk/dft_traffic_counts_aadf.zip
https://storage.googleapis.com/dft-statistics/road-traffic/downloads/data-gov-uk/dft_traffic_counts_aadf_by_direction.zip
https://storage.googleapis.com/dft-statistics/road-traffic/downloads/data-gov-uk/dft_traffic_counts_raw_counts.zip
```

## DfT observations and semantics

### AADF is an estimate, not a raw count

DfT defines Annual Average Daily Flow (AADF) as the average over a full year of
vehicles passing a point on each day. The AADF file has one row per
`count_point_id` and `year`, with `all_motor_vehicles` and vehicle-class AADFs.
The API example also exposes `estimation_method` and
`estimation_method_detailed`. The quality report describes these methods:

* `Counted` uses a 12-hour roadside count and an expansion factor;
* `Grown` applies a growth factor to the previous year's AADF;
* `ATC` uses an automatic-counter total;
* `Dependent` copies the adjacent link where a link is split at a local-authority
  boundary; and
* `Derived` is calculated from surrounding links for a very short or dangerous
  major-road link.

The downloads page says `Counted` is likely more accurate than `Estimated`, and
that estimated values must be used with caution. This is a quality signal, not an
instruction to discard estimated values.

The raw-count file is different: one row is the number of vehicles of each type
that passed on the actual `count_date`, for one `hour` and
`direction_of_travel`. It is not an AADF. Raw counts may support an investigation,
but must never be put into the AADF traffic band without a separately governed
conversion.

### Count year, retrieval date and freshness are different

Record all of the following independently:

* `observation_year`: the AADF `year` (or raw-count `year`);
* `count_date`: raw-count date, when present;
* `source_retrieved_at`: UTC retrieval timestamp in Source Export provenance;
* `publisher_release` and `effective_date`: the release identity supplied by the
  governed export; and
* `freshness_state`: derived by the active profile against a compile reference
  date.

`aadf_year` in the count-points API response (the equivalent bulk
`count_points.csv` column is `year`) is the latest year represented for a CP at the
time of that export; it does not turn an older AADF row into a current observation.
A value is `fresh` only when the configured profile's age rule is met. Otherwise
retain it as `stale`; do not silently substitute a newer-looking release date or
drop the row.

### Major and minor roads have asymmetric coverage

DfT produces a CP for every major-road link, but only a representative sample of
minor-road locations. The FAQ states that the minor-road sample is refreshed every
10 years and that many minor roads have never been in a sample. Therefore:

* no minor-road CP is **not** evidence of zero, low, safe or uncongested traffic;
* the normalized value must include `coverage_status: sampled | not_sampled |
  unknown`;
* a missing minor-road observation is published as `traffic_status: unknown` with
  an Evidence Request/diagnostic where appropriate; and
* a major-road observation is not automatically current merely because a CP
  exists—the AADF's year and estimation method still govern freshness and quality.

### Direction is explicit and cannot be inferred

The by-direction AADF file adds `direction_of_travel`. DfT defines `N`, `S`, `E`,
`W` and `C`; `C` means combined because separated directional flow is unavailable.
The API exposes the same value. The compiler must not infer a direction from line
vertex order, road-name suffixes or map orientation. Keep directional rows as
separate observations; use the combined `all_motor_vehicles` row for a two-way
traffic band where available. Do not sum different CPs to represent a corridor:
DfT's metadata explicitly warns that, for methodological reasons, AADFs from
different count points should not be added together.

## Source Export contract

Use the existing immutable `satn-source-export/v1` identity and its
`source_export_fingerprint`. DfT-specific layers should use one Source Export per
logical artifact (not one export for all DfT claims):

| `SourceExport` field | Required DfT value/meaning |
| --- | --- |
| `source_family` | `dft` (or a deployment-owned canonical equivalent) |
| `dataset` | `road-traffic-statistics` |
| `layer` | `count-points`, `aadf`, `aadf-by-direction`, `raw-counts` or `major-roads-database` |
| `publisher_release` | Exact DfT release/API snapshot identifier; never “latest” |
| `effective_date` | Effective/reference date supplied by that release; do not invent one when absent |
| `licence` | `OGL-UK-3.0` (the downloads page links to the Open Government Licence v3.0) |
| `format` | `zip+csv` for bulk files or `json` for an API response |
| `declared_crs` | Explicit CRS asserted by the governed acquisition. The DfT API docs expose latitude/longitude and easting/northing but do not declare a CRS; reject an ungoverned assumption and retain the traffic claim as spatially unresolved. |
| `raw_bytes_sha256` | SHA-256 of the exact ZIP/JSON bytes retained offline |

`provenance` must additionally pin:

* exact download URL or API endpoint, HTTP method, query parameters and page;
* `retrieved_at` in UTC, content type, byte count, ETag and Last-Modified when
  provided;
* archive member path(s), CSV header row and a schema/normalisation-contract
  fingerprint;
* the metadata/methodology URL used to interpret the layer;
* release or publication identifier, row count and any API pagination bound; and
* a content-addressed retained path.

The export fingerprint identifies the source identity and bytes. Retain the
separate provenance fingerprint already supported by the Local Evidence Store so
that two retrievals of identical bytes remain distinguishable by endpoint,
timestamp and acquisition receipt. Every normalized observation and map property
must point back to the Source Export fingerprint and a feature/row fingerprint.

## Normalized traffic observation

The following is the proposed implementation shape; field names are deliberately
close to the DfT names so an evidence query is inspectable:

```yaml
traffic_observation:
  observation_id: <stable hash of source export + row identity>
  source_export_fingerprint: <sha256>
  source_layer: aadf | aadf-by-direction | raw-counts
  count_point_id: <DfT CP id>
  observation_year: <integer>
  count_date: <ISO date or null>
  direction_of_travel: N | S | E | W | C | null
  road_name: <string>
  road_category: PM | PA | TM | TA | M | MB | MCU | null
  road_type: Major | Minor | null
  start_junction_road_name: <string or null>
  end_junction_road_name: <string or null>
  latitude: <decimal string or null>
  longitude: <decimal string or null>
  easting: <integer or null>
  northing: <integer or null>
  link_length_km: <decimal string or null>
  all_motor_vehicles: <integer or null>
  estimation_method: Counted | Estimated | null
  estimation_method_detailed: <DfT text or null>
  coverage_status: sampled | not_sampled | unknown
  freshness_state: fresh | stale | unknown
  match_state: matched | ambiguous | unmatched | unknown
  row_fingerprint: <sha256>
```

`null` means the field was not present in that source row; `unknown` is a
deliberate domain state and must not be rendered as zero. Preserve the full
vehicle-class columns even if the first traffic-band profile only uses
`all_motor_vehicles`.

## Matching a CP to a candidate segment

Matching must be deterministic and evidence-preserving, in this order:

1. **Explicit binding.** If a candidate segment has a governed
   `count_point_id`, bind only rows with that ID and the requested observation
   year/layer. The binding is still checked against the source row's road identity
   and geometry.
2. **Link identity.** Otherwise require a compatible tuple of road name/category,
   start-junction name and end-junction name when those attributes are available.
   Names are normalized for case and whitespace only; never fuzzy-match a
   different road name without an explicit profile rule.
3. **Geometry.** Transform the CP coordinate only through the declared CRS and
   test the CP point against the candidate geometry using a configured tolerance.
   The CP represents the whole DfT junction-to-junction link, while its coordinate
   is the count location; retain `coverage: full | partial | unknown` when the
   candidate covers only part of the link.
4. **Ambiguity.** If more than one candidate satisfies the rule, retain all
   candidate matches with `match_state: ambiguous` and issue a diagnostic. Do not
   attach the flow to the nearest candidate by distance alone.

The DfT CP table and AADF rows expose both coordinates and link lengths. The DfT
API examples show different coordinates for the CP details row and the AADF/raw
count row for the same CP; therefore a small coordinate difference is not proof of
a different link. Use the stable `count_point_id` and link identity first, and
retain all coordinate variants in provenance. Do not manufacture a line geometry
from a CP point. When the candidate's CRS or route geometry cannot be proven,
return `match_state: unknown` and retain the observation for inspection.

When a candidate intersects multiple DfT links, keep one observation per CP and
report a `traffic-multi-link` diagnostic. Never add AADFs across CPs to produce a
segment or route total. If a deployment needs a derived corridor statistic, that
must be a separately versioned, explicitly documented aggregation contract.

## Configurable traffic bands and freshness

Traffic bands belong to a versioned deployment profile, not to DfT. The profile
must contain:

```yaml
traffic_profile:
  profile_id: <stable name>
  version: <integer or semver>
  metric: all_motor_vehicles
  thresholds:
    - {id: low, upper_vehicles_per_day: <integer>}
    - {id: medium, upper_vehicles_per_day: <integer>}
    - {id: high, upper_vehicles_per_day: <integer>}
    - {id: very-high, upper_vehicles_per_day: null}
  high_traffic_challenge_band: very-high
  max_observation_age_years: <integer or null>
  stale_value_policy: retain-and-diagnose
  missing_policy: explicit-unknown
```

Thresholds must be sorted, non-overlapping and represented in canonical integer
vehicles/day units. A deployment may use different profiles for urban/rural or
road classes, but the selected profile ID/version and fingerprint must be in the
compile manifest. There is no DfT safety threshold in the sources above; any
default is a deployment policy and must be labelled as such.

Classification rules:

* use `all_motor_vehicles` from a matched AADF row; do not classify a raw count;
* classify the value even when `estimation_method` is estimated, retaining the
  quality flag and adding `traffic-estimated`;
* apply the profile age rule to `observation_year`, not retrieval date alone;
* retain stale values with `freshness_state: stale` and `traffic-stale`, rather
  than dropping them; and
* emit `traffic-unknown` where the value, match, coverage or CRS is unavailable.

## Conflicts and authority

Authority is resolved per traffic claim. A local highways-authority count and a
DfT AADF are different observations, not a reason to overwrite one another. When
two DfT exports or rows claim the same `(count_point_id, observation_year,
direction_of_travel)` but disagree on value, road identity, coordinates or
estimation method:

* preserve both observations and both Source Export fingerprints;
* mark the claim `conflicting` and emit `traffic-conflict` with field-level
  differences;
* let a versioned profile choose which claim is used for a band only if that
  precedence is explicit; and
* never average, silently select latest, or suppress the losing observation.

An unresolved conflict still yields a published candidate segment and an explicit
diagnostic. This is consistent with DfT's warning that link-level values may be
less robust and local sources may be more up to date.

## Selection diagnostic (not a veto)

Emit a compiler-authored diagnostic when all of these conditions hold:

1. the candidate alignment is explicitly `on-carriageway`;
2. a matched AADF classifies into the profile's `high_traffic_challenge_band`;
3. the observation is not `unknown` (a stale value remains usable but is marked
   stale); and
4. protected-space evidence for that segment is explicitly `absent`.

Recommended diagnostic ID: `traffic-high-on-carriageway-without-protected-space`.
Include the traffic observation ID, AADF year, band/profile fingerprint,
estimation/freshness state and protected-space evidence IDs. The diagnostic
challenges the alignment for human review; it does not remove, reject or
reclassify the candidate. If protected-space evidence is `present`, do not emit
this challenge. If it is `unknown` or conflicting, emit a separate
`protected-space-evidence-unknown`/`protected-space-conflict` diagnostic and keep
the traffic observation visible; do not pretend that unknown means absent.

The same rule applies to minor-road segments only when a real matched observation
exists. A missing minor-road sample cannot trigger a low-traffic assumption or a
high-traffic challenge.

## Optional map/publication fields

Traffic fields are optional annotations on a route/segment feature. They should be
omitted or rendered as “unknown” when no evidence exists, never rendered as zero:

```text
traffic_aadf_all_motor_vehicles
traffic_observation_year
traffic_count_method
traffic_count_method_detailed
traffic_direction
traffic_band
traffic_freshness
traffic_coverage_status
traffic_match_state
traffic_source_export_fingerprint
traffic_observation_id
traffic_diagnostic_ids
```

Map popups should identify “AADF (vehicles/day)” and the reference year, distinguish
`Counted` from `Estimated`, state when data is stale or minor-road coverage is
unknown, and link to the source export/evidence record. No colour or line width
may imply a safety threshold without the active profile legend.

## Completion and failure rules

The deterministic compiler must complete when:

* a DfT endpoint is unavailable at compile time (there is no network fetch);
* a retained export is missing, corrupt, stale or schema-incompatible;
* CP geometry cannot be transformed because CRS is not governed;
* a minor-road candidate has no sampled CP;
* multiple CPs or exports conflict; or
* the optional traffic profile is absent.

In every case, retain the candidate segment, emit a stable diagnostic and represent
traffic as `unknown`/`conflicting`/`stale` as applicable. Only malformed required
compiler inputs should fail ordinary input validation; traffic evidence is an
optional enrichment and cannot be a hard gate.

## Follow-up unknowns

1. Confirm the exact DfT release/effective-date convention to use in each governed
   acquisition receipt; the public API and downloads page expose year values but do
   not define a universal release-date field.
2. Confirm the CRS asserted by the acquisition path for DfT `easting`/`northing`
   values before enabling coordinate-based matching. The API documentation does
   not declare one.
3. Decide the deployment's policy thresholds and maximum age; no DfT source here
   supplies a safety threshold.
4. Define the protected-space evidence vocabulary and how an alignment is marked
   `on-carriageway` in the active network model.
5. Add a governed aggregation contract only if a future requirement needs a
   corridor-level statistic; current DfT metadata prohibits summing CP AADFs.
