# West Midlands Combined Authority area

This deployment covers the seven WMCA members with full voting rights:

- Birmingham City Council
- City of Wolverhampton Council
- Coventry City Council
- Dudley Metropolitan Borough Council
- Sandwell Metropolitan Borough Council
- Solihull Metropolitan Borough Council
- Walsall Metropolitan Borough Council

The official WMCA membership page distinguishes these authorities from reduced-
voting members. The latter are deliberately not included in `area.yaml`:

- [WMCA — Who we are](https://www.wmca.org.uk/who-we-are/)
- [WMCA Single Assurance Framework — governance](https://www.wmca.org.uk/documents/saf/single-assurance-framework-november-2025/single-assurance-framework-2025/single-assurance-framework-november-2025/3-governance-and-decision-making/)

The boundary is acquired from the seven named OSM administrative areas. The
required core snapshot (boundary, places and routable network) is prepared by
the existing OSM adapter. Cycle-route evidence uses the existing public layers:

- [National Cycle Network public FeatureServer](https://services5.arcgis.com/1ZHcUS1lwPTg4ms0/arcgis/rest/services/National_Cycle_Network_Public/FeatureServer)
- [Reclassified Routes public FeatureServer](https://services5.arcgis.com/1ZHcUS1lwPTg4ms0/arcgis/rest/services/Reclassified_Routes_Public/FeatureServer)

The OSM core snapshot was acquired and validated as
`wmca-osm-2026-09-06-constituent-authorities`; the configured target adds the
governed WMCA clip of the same source release as
`wmca-osm-2026-09-06-constituent-authorities-open-roads-2026-04-07`.
The preparation commands are:

```shell
uv run satn snapshot deployments/wmca/area.yaml
uv run satn compile deployments/wmca/area.yaml --full
```

Elevation is not declared because no WMCA-scoped licensed terrain file was
available. Missing optional evidence must remain explicit in the resulting
review.

The configured official road input is the WMCA clip at
`data/governed/wmca-os-open-roads-2026-04-07.geojson`, extracted from [OS Open
Roads](https://osdatahub.os.uk/downloads/open/OpenRoads). Ordnance Survey lists
the product as Great Britain coverage, available under the Open Government
Licence, with GeoPackage among the supported formats. OSM road geometry is not
a substitute for that official classification input.
