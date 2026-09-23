# Candidate neighbourhood evidence

Candidate neighbourhoods identify areas for further consideration of through
motor-traffic removal. They do not assert an existing low-traffic scheme,
connected internal streets, safe crossings, or legal access.

Preparation uses a separate built-up-area GeoJSON input for these candidates:

```yaml
source:
  candidate_built_up_areas: ../../urban-ltn-wayfinder/ons-bua-2022-candidate-input.geojson
```

The path is resolved relative to the area YAML file. The optional file is a
GeoJSON FeatureCollection of Polygon or MultiPolygon features. Each feature
needs ONS `BUA22CD` and `BUA22NM` properties. The annotated ONS input also keeps
`source_id`, `effective_date`, `licence`, `source_url`, and `attribution` on each
feature; that provenance is retained on published candidates. If the option is
omitted, preparation produces no candidate neighbourhoods. Place or
administrative boundaries do not substitute for built-up evidence.

For each sourced built-up area, preparation polygonizes its boundary with
official A Roads, B Roads, and Classified Unnumbered Roads. A candidate face
must be fully inside that built-up area and have positive-length boundary
frontages on at least two distinct classified roads. A/B road numbers identify
those roads even when their names vary across segments. An officially named
Classified Unnumbered Road can also identify a frontage. Feature segment IDs
and unnamed roads do not establish separate roads, and a point contact is not
a frontage. The built-up edge may close the remaining sides of an area.

The geometry uses exact source intersections. Preparation does not add a
buffer, snap, density rule, size limit, or angle rule. The rural routing path
continues to use `Place.urban_extent`; the candidate-only input does not replace
that evidence.

The published candidate inspector shows the built-up area, measured area,
classified-road frontages, whether the built-up edge closes part of the
boundary, and the source dataset/date/licence evidence.
