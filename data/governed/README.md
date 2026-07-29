# Governed source extracts

## OS Open Roads, 7 April 2026

`banes-os-open-roads-2026-04-07.geojson` is the `road_link` layer from the
Great Britain OS Open Roads GeoPackage published on 7 April 2026, spatially
clipped to the governed B&NES boundary. Only the stable OS feature identifier,
road classification and geometry are retained because those are the fields the
original B&NES compiler path governs.

`weca-os-open-roads-2026-04-07.geojson` is the equivalent extract clipped to
the governed West of England boundary. It also retains road function,
classification number and primary name so official-versus-OSM disagreements
remain inspectable. Both extracts come from the same national package; their
retained schemas reflect those distinct governed uses.

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
