# Governed source extracts

## OS Open Roads, 7 April 2026

`banes-os-open-roads-2026-04-07.geojson` is the immutable historical B&NES
snapshot input. It is spatially clipped to the governed B&NES boundary and
retains only the stable OS feature identifier, road classification and geometry.
Its SHA-256 remains
`bb39710d078c52366fc0c75205cbd396db6d408f96f43bfb216ba15613d82f5b`;
it is not reinterpreted as a v1 Source Export and is not modified.

`weca-os-open-roads-2026-04-07.geojson` is the equivalent extract clipped to
the governed West of England boundary. It also retains road function,
classification number and primary name so official-versus-OSM disagreements
remain inspectable. Its immutable raw SHA-256 is
`87c944fb4c4f77c949f25913c58b3e7f49df80bbe0bf317606b32feb0653e89c`.
It is the current v1 OS Open Roads Source Export for both WECA and B&NES.

B&NES does not create a second raw source by joining attributes onto its legacy
file. Snapshot acquisition selects RoadLinks from the unchanged WECA export
using `intersects` against the governed B&NES boundary and retains each complete
source geometry. The derived B&NES snapshot records the source and boundary
checksums, predicate, whole-feature rule and selected count. ADR 0017 and
`banes-open-roads-v1-migration.json` record the deliberate lineage and geometry
migration.

- Product: [OS Open Roads](https://www.ordnancesurvey.co.uk/products/os-open-roads)
- Download API: `https://api.os.uk/downloads/v1/products/OpenRoads/downloads?area=GB&format=GeoPackage&redirect`
- National package: `oproad_gpkg_gb.zip`
- National package MD5: `2ee5d30899c4a44df321e5e1a66989ac`
- Classification vocabulary: A Road, B Road, Classified Unnumbered,
  Unclassified, Not Classified and Unknown
- Licence: Open Government Licence v3.0
- Attribution: Contains OS data © Crown copyright and database rights 2026.

The national package is not stored in this repository. The compact governed
extract is committed so snapshot acquisition and publication remain
reproducible without a multi-gigabyte download.
