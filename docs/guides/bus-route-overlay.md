# Prepare a bus context overlay

`scripts/prepare_bus_overlay.py` writes an independent GeoJSON overlay for scheduled bus route shapes and, when supplied, source-identified NaPTAN stop areas. It does not compile roads or match bus shapes to road sections.

The fresh mode reads a local DfT Bus Open Data Service South West GTFS ZIP, applies `calendar.txt` and `calendar_dates.txt` for the requested service date, keeps `route_type=3` trips, orders their shape points by `shape_pt_sequence`, and clips linework to the supplied governed boundary. Shape-less or unusable active bus trips are listed in `provenance.skipped_services`; they do not imply that no bus service exists. The output records the feed version and hash, service date, boundary hash, source URL, licence, and attribution.

For the retained current inputs in this workspace, run:

```sh
uv run python scripts/prepare_bus_overlay.py \
  --gtfs build/bus-overlay/itm_south_west_gtfs.zip \
  --service-date 2026-09-23 \
  --boundary data/snapshots/weca-osm-current/boundary.geojson \
  --naptan-xml build/bus-overlay/naptan-west-of-england.xml \
  --output build/bus-overlay/weca-bus-context.geojson
```

The `--naptan-xml` input is optional. It contributes only active `GBCS` and `GPBS` `StopArea` records that have usable point coordinates inside the same boundary. The feature properties preserve the source `StopAreaCode`, name, type code, status, and source creation date. The official DfT schema guide maps `GBCS` to “bus or coach station” and `GPBS` to “paired on-street bus stops” ([NaPTAN Schema Guide 2.4](https://naptan.dft.gov.uk/naptan/schema/2.4/doc/NaPTANSchemaGuide-2.4-v0.57.pdf); [DfT guide for data managers](https://www.gov.uk/government/publications/national-public-transport-access-node-schema/naptan-guide-for-data-managers)). A stop-area point describes a named source grouping; it does not assert that passenger transfers were observed. Missing coordinates and out-of-boundary records are counted in `provenance.naptan_diagnostics`; no point is inferred from nearby stops.

The alternative retained-import mode copies previously retained line features, adds `kind="bus-route"`, preserves their other feature attributes, and requires the matching snapshot metadata so the output keeps its recorded historical service date and source provenance. For example, from a checkout where the retained KRN evidence is available:

```sh
uv run python scripts/prepare_bus_overlay.py \
  --retained-geojson /path/to/agentic-krn-compiler/examples/weca/evidence/bus-routes.geojson \
  --snapshot /path/to/agentic-krn-compiler/examples/weca/evidence/snapshot.json \
  --output build/bus-overlay/weca-bus-context-retained.geojson
```

The retained-import output is marked `freshness="historical"`; it never substitutes the snapshot service date with today’s date. The feed and NaPTAN datasets are listed as Open Government Licence material by the [DfT bus feed](https://data.bus-data.dft.gov.uk/timetable/download/gtfs-file/south_west/) and [DfT NaPTAN dataset record](https://findtransportdata.dft.gov.uk/dataset/national-public-transport-access-nodes-17f0ce86368).

## Attach it to an existing publication

Pass the prepared overlay to the Rust publisher with the path to an existing publication. Replace the placeholders with those two paths:

```sh
cargo run --manifest-path rust/Cargo.toml -- \
  --output <existing-publication> \
  --bus-context <overlay.geojson>
```

This attaches the bus context layer to the existing published network and its decisions; it does not rerun the planner or model.
