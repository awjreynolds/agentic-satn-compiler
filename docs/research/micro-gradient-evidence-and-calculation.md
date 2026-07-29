# Micro-gradient evidence and calculation

Research for [Establish the evidence basis for micro-gradient analysis](https://github.com/awjreynolds/agentic-satn-compiler/issues/74).

- Retrieved: 2026-07-23
- Research branch: `research/micro-gradient-evidence`
- Scope: evidence and derivation decisions, not production implementation

## Conclusion

Use a council-generic elevation-source contract. For English councils, the Environment Agency's England-wide 1 m LIDAR Composite Digital Terrain Model is the leading governed baseline, subject to a coverage and survey-index check for each study extent. B&NES is a validation fixture, not a source dependency.

Do not require council-owned elevation data. A local survey may be a higher-priority optional source when it conforms to the same contract, but the generic tool must work from broadly available jurisdictional data and must expose when that data cannot support the requested analysis.

Do not derive user-facing gradient from the terrain tiles used to draw a 3D map. Ingest the governed DTM separately, preserve its source metadata, and derive gradients along each Published Feature.

The interface should default to a 50 m sustained-gradient view and allow a 20 m detail view where the evidence supports it. Those are rolling windows on a regularly sampled profile, not fixed 20 m or 50 m bins. A fixed bin can hide a steep section when it straddles a boundary.

The result is analytical evidence with disclosed limitations, not a surveyed carriageway or path long-section.

## Primary source facts

### Environment Agency LIDAR Composite DTM, 1 m

The Environment Agency dataset record states:

- approximately 99% coverage of England;
- 1 m spatial resolution;
- bare-earth DTM produced by removing surface objects from the DSM;
- a composite of timestamped archive and National LIDAR Programme surveys;
- the newest and best-resolution survey is used where surveys repeat;
- bilinear interpolation is used where source data is resampled;
- GeoTIFF delivery in 5 km OS National Grid tiles;
- elevations in metres relative to Ordnance Datum Newlyn, using OSTN15;
- individual surveys entering the composite have a reported vertical accuracy of ±15 cm RMSE;
- a survey metadata index is supplied and must be consulted to determine the source survey at a location;
- Open Government Licence applies, with Environment Agency attribution.

Source: [LIDAR Composite Digital Terrain Model (DTM) - 1m](https://www.data.gov.uk/dataset/01b3ee39-da3f-47b6-83da-dc98e73a461f/lidar-composite-digital-terrain-model-dtm-1m).

Licence text: [Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).

Important interpretation:

- 1 m is cell spacing, not a promise that a gradient over 1 m is accurate.
- ±15 cm RMSE is a survey-level vertical-accuracy statement, not a bound on the derived slope of every road or path.
- The DTM represents modelled ground. It commonly does not represent the travelled deck of a bridge, and a tunnel route cannot be inferred from surface terrain.
- Composite survey age varies. The survey index is part of the evidence, not optional metadata.

### Coverage remains a per-deployment gate

National coverage is not proof that every Published Feature in every council area has suitable data. Before enabling a micro-gradient capability for a deployment:

1. intersect the current Published Feature extent with the Environment Agency coverage/index service;
2. record missing cells and source-survey dates;
3. inspect a sample of bridges, tunnels, cuttings, steep streets and paths;
4. define a fallback or `insufficient elevation evidence` state.

This research did not complete the B&NES fixture's live extent query, so it must not be represented as a confirmed assumption.

### Generic source contract

The elevation input should be selected by configuration, not council-specific code. Each source adapter should declare:

- jurisdiction and geographic extent;
- licence and required attribution;
- horizontal grid spacing and stated vertical accuracy;
- horizontal and vertical reference systems;
- source date/index availability;
- sampling/interpolation support;
- missing-data semantics;
- whether the source represents bare earth, surface, or a surveyed route;
- maximum defensible rolling-window detail.

An analysis capability is derived from these declarations and validation results. A source that is too coarse for 20 m or 50 m sections may still support a route-level elevation overview, but the interface must disable or relabel unsupported micro-gradient tracks.

The Environment Agency source provides a broadly available England adapter. Equivalent authoritative sources for other intended jurisdictions must be separately validated before those jurisdictions are claimed as supported. A coarse global DEM should not be used merely to make the feature appear universally available.

## Current B&NES fixture state

At commit `6ba9228`:

- `src/satn/topography.py` can build `Topography Profile` and `Gradient Sections`;
- a Gradient Section records start/end distance, length, forward and absolute gradient, band, uphill direction and evidence identifiers;
- adjacent sections with the same band and direction can merge;
- `TopographyConfig` contains bands and sustained-length triggers;
- the default maximum elevation sample gap is 250 m;
- `config/banes.yaml` does not configure a governed national elevation source;
- `data/snapshots/banes-osm-current/snapshot.json` records `"elevation": null`.

The current B&NES fixture therefore has a display contract for gradient evidence but no governed elevation evidence feeding it. Its pairwise sample derivation is also not the proposed 20 m/50 m rolling-window model. The correction belongs in a generic source adapter and derivation contract, not a B&NES-only ingestion path.

## Proposed derivation contract for the decision ticket

These are research recommendations, not yet accepted domain policy.

### 1. Build a governed profile

- Reproject Published Feature geometry and the DTM into a suitable metric CRS.
- Densify the feature centreline at a regular 5 m interval. A 10 m interval may be an acceptable performance fallback, but should be tested against known steep locations.
- Sample the DTM consistently, recording the raster tile, source survey/index reference and interpolation method.
- Retain distance from the chosen Published Feature start, elevation, source reference, and quality flags for every sample.
- Never silently interpolate across missing evidence or a gap larger than the accepted contract.

### 2. Calculate rolling gradients

For window length `w`, calculate:

`gradient_percent = 100 * (elevation(d + w/2) - elevation(d - w/2)) / w`

Use:

- 50 m as the default sustained-gradient track;
- 20 m as the detail track for local changes;
- raw/pairwise slope only for diagnostics, not the default visual encoding.

At feature ends and joins, calculate across the ordered Gradient Inspection Path where adjoining geometry and evidence are continuous. Otherwise flag a boundary rather than manufacturing a complete window.

### 3. Form Gradient Sections

- Classify every rolling-window result using the accepted gradient bands.
- Apply a small hysteresis or minimum-run rule so noise around a threshold does not create rapid colour flicker.
- Preserve local maxima; do not average an entire Published Feature into one value.
- Store the exact window and method on every evidence output.
- Test the existing bands against representative B&NES routes before treating them as final policy.

### 4. Represent uncertainty

Attach explicit flags for:

- bridge or elevated deck;
- tunnel or covered route;
- cutting, embankment or retaining structure;
- centreline displaced from the travelled alignment;
- steps or non-ridable section;
- missing or old source survey;
- unusually abrupt elevation discontinuity;
- feature join with incompatible direction or geometry.

The panel should render these as gaps, hatching or warning intervals. It should not show a precise coloured gradient through a section the DTM cannot represent.

## Validation fixture

Before accepting the analytical contract, run the same fixture suite against representative councils/source adapters. For the initial B&NES fixture:

- choose known flat, rolling, steep and very steep routes;
- include at least one bridge, tunnel/covered section, cutting and stepped path;
- compare 5 m and 10 m sampling;
- compare rolling 20 m and 50 m results;
- compare selected sections with an independent surveyed or trusted road-profile source where available;
- verify path reversal changes the sign/uphill direction but not absolute gradient;
- verify joins do not double-count distance or create a synthetic spike.

## Implications for the map

- The 3D terrain may use the same broad source family, but it is still a display aid and must not become the analytical evidence channel.
- Terrain exaggeration can improve visual legibility, but it multiplies displayed height; it does not alter the derived gradient.
- A 1:10 slope is 10%. Over 50 m it rises only 5 m, so it will remain visually subtle at a district-scale camera unless vertically exaggerated.
- The Linear Evidence Panel is the correct place to communicate the actual gradient and its distance window.

## Remaining decisions and unknowns

- Confirm exact coverage and survey dates for every validation fixture, starting with B&NES.
- Define the initially supported jurisdictions and validate an authoritative adapter for each.
- Decide the capability threshold below which 20 m, 50 m, or all micro-gradient analysis is disabled.
- Decide whether 5 m or 10 m is the governed sampling interval.
- Decide the accepted 20 m/50 m calculation and edge treatment.
- Decide hysteresis/minimum-run parameters and final gradient bands.
- Decide whether a separate road-surface or engineering long-section source is needed for structures.
- Decide how evidence quality appears in the panel without overwhelming the primary gradient reading.
