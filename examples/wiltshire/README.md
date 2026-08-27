# Wiltshire map source

This deployment is built from real source data:

- Wiltshire unitary-authority boundary: Office for National Statistics,
  **Counties and Unitary Authorities (December 2025) Boundaries UK BGC**, queried
  from the official ArcGIS FeatureServer.
- Roads, cycleways, paths and named places: OpenStreetMap data in the Wiltshire
  extract published by GEO2day, with data current to 24 July 2026. OSM data is
  used under the Open Data Commons Open Database License (ODbL).

The OSM extract is converted into the compiler's local snapshot format by
`scripts/extract_wiltshire_osm.py`. The extraction keeps real OSM geometries and
tags; it does not draw or infer routes between settlement centroids.

The map remains an analytical SATN review, not an adopted plan, legal access
finding, safety audit, or scheme design. Missing evidence such as elevation,
traffic counts, schools, and official cycle-route records remains absent.

To reproduce the local source conversion after downloading the two source files:

```shell
uv pip install --python .venv/bin/python osmium
.venv/bin/python scripts/extract_wiltshire_osm.py \
  --pbf /path/to/wiltshire.osm.pbf \
  --boundary /path/to/wiltshire-boundary.geojson \
  --output examples/wiltshire/source
uv run satn snapshot deployments/wiltshire/area.yaml
uv run satn compile deployments/wiltshire/area.yaml --full
uv run python scripts/publish_site.py deployments/wiltshire/area.yaml
```
