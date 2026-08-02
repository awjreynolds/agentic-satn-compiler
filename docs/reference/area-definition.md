# Area Definition reference

An Area Definition is the data-only root configuration for one compilation. Load it
through `satn.models.AreaDefinition`; do not treat unvalidated YAML as configuration.

Use `examples/new-area/area.yaml` as the smallest parseable starter and
`deployments/banes/area.yaml` as the flagship real deployment.

## Top-level identity

| Field | Purpose |
| --- | --- |
| `area_id` or legacy `council_id` | Stable geographic compilation identity. |
| `area_name` or legacy `council_name` | Human-readable name. |
| `deployment_id` | Stable catalogue/publication identity when deployed. |
| `source` | Boundary, places, network, source exports and immutable snapshot identity. |
| `compilation` | Network, evidence, selection, topography and agent profiles. |
| `publication` | Atomic output destination, title, audience and presentation settings. |
| `atm` | Optional governed comparison reference and redistribution controls. |

## Source block

`source.kind` is `fixture` for committed synthetic evidence or `osm` for the governed
OSM acquisition adapter. A real source block declares:

- boundary/place queries and buffer;
- immutable `snapshot_dir` and unique `snapshot_id`;
- network mode and remote endpoints;
- place classes used for communities and urban scope;
- optional official road classification;
- current/reclassified NCN services;
- optional national elevation evidence; and
- any retained-core lineage used by an explicit migration/bootstrap workflow.

The source hierarchy resolves claim evidence. It does not select a route.

## Compilation block

Important profiles include:

- maximum connection distance and network-scope policy;
- Network Selection Profile: candidate reuse order, comparator order, material
  differences, displacement rules, unknown-value behaviour and stable tie-break;
- topography sampling and material total-elevation-change rules;
- population, education and traffic evidence profiles;
- bounded hybrid/candidate/transition limits; and
- agent provider, response mode and review statuses.

All council-specific thresholds belong in versioned profile data. Missing optional
facts remain unknown; they do not receive zero or favourable defaults.

## Publication block

`publication.output_dir` must remain inside the allowed workspace unless a caller has
explicit external-destination authority. `audience: public` activates public evidence
and redistribution safeguards. Compilation success does not require public
publication.

## Validate a definition

```shell
uv run python -c "from satn.models import AreaDefinition; a=AreaDefinition.from_yaml('path/to/area.yaml'); print(a.area_id)"
```

Changing a governed input, profile or evidence fingerprint produces a new compilation
identity. Never keep a previous provenance lock by hand after such a change.
