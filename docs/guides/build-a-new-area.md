# Build a map for a new area

This guide creates a new compiler input and Reviewable Network. It does not copy a
council identity or snapshot ID from B&NES.

## 1. Start with a valid local template

Copy the parseable starter rather than assembling YAML from snippets:

```shell
cp examples/new-area/area.yaml examples/new-area/my-area.yaml
uv run python -c "from satn.models import AreaDefinition; print(AreaDefinition.from_yaml('examples/new-area/my-area.yaml').area_id)"
```

The starter uses committed synthetic evidence so its shape can be validated. Before a
real run, replace its identity, source and publication paths and choose a unique,
versioned `snapshot_id`.

## 2. Define the scope and obligations

Record:

- one coherent authority or regional boundary;
- community place classes and external buffer;
- schools, healthcare, universities and other configured strategic destinations;
- the network and publication identities; and
- the role responsible for governed local decisions.

An authority boundary scopes evidence; it does not split a continuous regional
network or prove that a route is legally usable.

## 3. Build the evidence inventory

| Class | Examples | Rule |
| --- | --- | --- |
| Required governed core | boundary, places, routable network | Snapshot must validate before compilation. |
| Reusable-asset evidence | cycleways, current/reclassified NCN, Greenways, PROWs, former railways, Local Connectors | Preserve source identity and unknown access/condition separately. Status does not prove suitability. |
| Strategic destinations | schools, healthcare, universities, retail centres | Missing optional registers remain explicit; do not invent entrances or counts. |
| Selection evidence | topography, DfT traffic, population, protected-space claims | Version profile thresholds and retain missing/conflicting states. |
| Controlled/local | officer evidence, licensed comparison geometry | Keep outside public artifacts unless redistribution is explicitly permitted. |
| Optional context | boundaries, terrain display, prior plans | Absence cannot prevent valid-input compilation. |

Every observation needs a source family, stable publisher/source identifier where
available, effective or observation date, licence, coverage and content fingerprint.
Conflicting claims remain attached; the source hierarchy resolves claims, not policy.

## 4. Configure policy as data

Use the [Area Definition reference](../reference/area-definition.md) to configure:

- topography and evidence-spacing rules;
- the Network Selection Profile and its reuse-class order;
- material displacement thresholds;
- source authority/fallback hierarchies;
- bounded agent review statuses and response mode; and
- publication audience and output paths.

Do not add council constants to compiler code. A lower reuse class may displace a
higher eligible class only through a configured, evidenced Material Displacement
Reason.

## 5. Snapshot and compile

```shell
uv run satn snapshot path/to/area.yaml
uv run satn compile path/to/area.yaml --full
```

The compiler generates finite choices, applies deterministic comparisons and uses an
agent only for configured material ambiguity. An agent cannot fetch unspecified raw
facts, invent geometry or return an unoffered choice.

Valid inputs always produce a Reviewable Network. When continuity or evidence is
insufficient, the output retains Network Gaps, evidence requests and limitations.

## 6. Inspect before iterating

Review the strategic map first, then add assets, candidates, traffic and contextual
layers. Check:

- every Access Obligation is served or represented by a gap;
- existing assets are accounted for even when unused or unconnected;
- unselected substitutes retain their reason and change conditions;
- complementary routes are not presented as rejected substitutes;
- officer decisions were applied and material divergence is visible; and
- intervention states do not claim cost, legal authority or design readiness.

Record new officer decisions as versioned scenario input. They do not expire. A
recompile may disagree, but must show the divergence rather than silently replace the
decision.

## 7. Package and optionally publish

Local compilation is already a successful map build. If the deployment is intended
for a catalogue or Pages, follow [Package and publish](publish-a-deployment.md).
