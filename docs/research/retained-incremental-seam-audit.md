# Retained incremental seam audit (Issue #339)

## Decision

The repository has implemented identity, validation and selective-store seams,
but not an end-to-end retained incremental compiler. Keep ordinary `satn compile`
as the authority comparator. Add a separate presentation-only publication seam
before changing whole-network reuse, then wire persisted stage materialisations
into `iterate_scenario` only after the ADR-0012 equivalence gates pass.

## What exists today

### Scenario Iteration coordinator

[`src/satn/scenario_iteration.py`](../../src/satn/scenario_iteration.py) defines
immutable `ScenarioStageRecord` values for stages 1–6, requiring SHA-256 input,
output, dependency and upstream lineage ([lines 64–115](../../src/satn/scenario_iteration.py#L64-L115)).
`ScenarioIterationState` requires exactly those six stages, one dependency
manifest, and a derived state fingerprint ([lines 118–165](../../src/satn/scenario_iteration.py#L118-L165)).
Stage-7 configuration includes area, criteria, selection-profile, reusable-state,
dependency and publication-configuration fingerprints; the accepted ledger binds
to the state’s evidence and assembly fingerprints ([lines 168–251](../../src/satn/scenario_iteration.py#L168-L251)).

`iterate_scenario()` validates the dependency manifest and stale state/ledger,
marks stages 1–6 as validated hits, calls a supplied scenario compiler for stage
7, and calls a supplied atomic publisher for stage 8 ([lines 341–428](../../src/satn/scenario_iteration.py#L341-L428)).
The receipt requires artifact digests plus explicit whole-publication validation
and atomic replacement ([lines 254–281](../../src/satn/scenario_iteration.py#L254-L281)).
The focused test proves the intended disposition (`validated-hit` ×6,
`recomputed` ×2) and that evidence refresh/routing are not called
([`tests/test_scenario_iteration.py`](../../tests/test_scenario_iteration.py#L83-L125)).

This is a callback seam, not a compiler integration: no source module calls
`iterate_scenario`; its stage records are not persisted by the Local Evidence
Store, and there is no Scenario Iteration CLI command.

### Evidence and reusable materialisations

`LocalEvidenceStore` is the physical seam. It transactionally resolves typed Edge
Enrichments, verifies them, and retains Scenario enrichment citations
([`local_evidence_store.py`](../../src/satn/local_evidence_store.py#L537-L595)).
Its schema has source exports, ingestion contracts, partition content/attestations,
coverage-state history/current pointer and typed enrichment tables, but no tables
for area views, canonical networks, routing candidates or assemblies
([`local_evidence_store.py`](../../src/satn/local_evidence_store.py#L1337-L1420)).

The logical records do exist as pure immutable values:

- `AreaExtractionMaterialisation` fingerprints area geometry, predicate, coverage
  states/attestations, availability and selected/rejected feature identities
  ([`evidence_materialisations.py`](../../src/satn/evidence_materialisations.py#L95-L178)).
- `AreaNetworkMaterialisation` binds that extraction to a normalisation contract
  and stable canonical edges ([`evidence_materialisations.py`](../../src/satn/evidence_materialisations.py#L181-L279)).
- `RoutingAssemblyMaterialisation` snapshots exact routing candidates, assembly
  records, visible gaps and diagnostics from a compiled result, but explicitly
  does not route, mutate `CompiledNetwork` or grant publication authority
  ([`routing_materialisation.py`](../../src/satn/routing_materialisation.py#L1-L7),
  [`routing_materialisation.py`](../../src/satn/routing_materialisation.py#L228-L315)).
- `EdgeEnrichmentStore` resolves exact request fingerprints, stores typed values,
  and verifies scenario citations against kind, edge identity, geometry and value
  fingerprints ([`edge_enrichments.py`](../../src/satn/edge_enrichments.py#L482-L614)).

These records are not consumed by the ordinary network compiler. The optional
Python `evidence_store`/`evidence_state` path verifies an exact coverage state,
then still calls `load_snapshot()` and `compile_network()` ([`pipeline.py`](../../src/satn/pipeline.py#L634-L710)).
The CLI exposes `compile --full` and publication-authority options, but no paired
evidence-state or Scenario Iteration route ([`cli.py`](../../src/satn/cli.py#L63-L130)).

### Publication artifacts and current reuse

`publication_artifacts()` names the stable output set: GeoPackage, GeoJSON,
asset-accounting files, `run.json`, agent/divergence/intervention records,
backbone comparison, review-map index/ZIP and PDF, with optional reviewable and
strategic sidecars ([`publisher.py`](../../src/satn/publisher.py#L175-L200)).
`run.json` records schema/status, decision ledgers, governed/input fingerprints,
snapshot and Area Definition hashes, the full compilation dependency manifest and
semantic diagnostics ([`publisher.py`](../../src/satn/publisher.py#L2222-L2356)).
`publish()` writes all artifacts to a temporary sibling, validates them, then
atomically replaces the destination ([`publisher.py`](../../src/satn/publisher.py#L391-L499)).

The existing reuse path is whole-publication reuse only. It rejects `--full`,
missing/malformed or stale ledger records, governed-input or dependency-manifest
mismatches, and input-fingerprint mismatches; it then checks UI assets and runs
`validate_publication()` before returning the prior result
([`pipeline.py`](../../src/satn/pipeline.py#L1640-L1739)).

## Why presentation-only WECA changes still compile the network

The current UI check compares exact bytes of MapLibre and review-map assets and
the fingerprinted JS/CSS filenames in the published HTML
([`pipeline.py`](../../src/satn/pipeline.py#L1610-L1637)). A changed review lens
(commit `416afcb`, “Replace evidence panel with review lens”, issue #337) makes
that check false. `_reuse_validated_publication()` logs “republishing” and returns
`None` before publication validation; `_compile()` consequently loads the snapshot,
builds the network and executes the normal publication writer
([`pipeline.py`](../../src/satn/pipeline.py#L660-L710)).

The semantic dependency manifest deliberately classifies review-map assets as
excluded compiler components, while retaining their reasons and requiring current
publication validation ([`compilation_dependencies.py`](../../src/satn/compilation_dependencies.py#L244-L275)).
That separation explains the otherwise surprising result: the semantic `run_id`
can remain stable while a UI-byte mismatch still forces the full compiler path.

The regression test records the observable consequence: after only changing
`review-map.js`, the refreshed publication retains the same semantic `run_id`,
but `publication_reused` is absent and the new script is published
([`tests/test_recompilation.py`](../../tests/test_recompilation.py#L69-L102)).
Thus a presentation-only WECA update cannot use the 4.70-second identical-input
reuse path. The historical WECA run is explicitly incomplete (it reached
publication after 4,801.6 seconds without an atomic result), so it is evidence of
the cost/risk of the full path, not a completed performance baseline
([`docs/research/weca-scale-benchmark-2026-07-27.md`](weca-scale-benchmark-2026-07-27.md#L9-L21)).

## Lifetimes and invalidation

| Artifact | Current lifetime | Invalidation / reuse boundary |
| --- | --- | --- |
| Source Export, partition content and attestation | Raw source paths and registry rows are retained; coverage states are immutable historical rows with a current pointer. Rebuild reconstructs a sibling store and preserves historical fingerprints ([`local_evidence_store.py`](../../src/satn/local_evidence_store.py#L774-L913), [`local_evidence_store.py`](../../src/satn/local_evidence_store.py#L1007-L1039)). | Exact source export + ingestion contract + partition key reuses a cell. Explicit source replacement rematerialises only that layer/cell; changed attestations invalidate dependent materialisations (ADR 0011, [lines 102–116](../adr/0011-stable-evidence-partition-and-dependency-identities.md#L102-L116)). |
| Area extraction / canonical network | Frozen Python records only; no Local Evidence Store persistence or compiler consumer. | Area geometry, predicate, coverage/attestation, feature lineage or normalisation change changes the fingerprint (records above; ADR 0012 stage contracts [lines 51–56](../adr/0012-scenario-iteration-stage-graph-and-reuse.md#L51-L56)). |
| Edge Enrichment / citation | Immutable typed DuckDB rows; citation rows are retained when a Scenario cites an enrichment. | Geometry, partition attestation, algorithm/dependency or parameters create a new enrichment; stale or incomplete lookup is a miss (ADR 0014 [lines 221–260](../adr/0014-edge-enrichment-lifecycle-and-storage-contract.md#L221-L260)). |
| Scenario stages 1–6 / assembly | `ScenarioStageRecord` and `ScenarioIterationState` are caller-owned immutable values; no persisted state or assembly materialisation is wired. | Any stage/dependency lineage mismatch fails closed in `iterate_scenario`; criteria/profile/ledger changes are intended to recompute stage 7 only (ADR 0012 [lines 74–95](../adr/0012-scenario-iteration-stage-graph-and-reuse.md#L74-L95)). |
| Publication bundle | A complete output directory is a materialisation replaced atomically; `run.json` is the reuse manifest. | Any governed input, decision, compiler dependency, validator or artifact/template dependency mismatch requires a new publication. UI-byte mismatch currently falls through to full compile, even when semantic `run_id` is unchanged. |

## Smallest integration gaps and recommendation

1. **Persist stage state.** Add versioned sidecar manifests/tables for stages 1–6,
   area extraction, canonical network and routing/assembly, with complete upstream
   references and validators. Do not infer them from legacy snapshots or database
   bytes. This is the missing bridge identified by ADR-0012’s migration slices
   ([lines 99–125](../adr/0012-scenario-iteration-stage-graph-and-reuse.md#L99-L125)).
2. **Connect the coordinator to real adapters.** Replace the test callback pair in
   `iterate_scenario()` with adapters that resolve persisted materialisations,
   invoke the existing stage-7 compiler, and call the real publisher. Keep the
   current snapshot path unchanged until fixture, B&NES, A4017 and complete WECA
   equivalence gates pass (ADR 0012/0015).
3. **Add a presentation-only publisher.** Split the review-map asset/template
   fingerprint from semantic compilation identity; when only that fingerprint
   changes, validate the existing semantic publication, regenerate the review-map
   shell/ZIP from its retained data, and atomically replace only the presentation
   files. Do not weaken `_review_map_assets_are_current()` or whole-publication
   validation by simply ignoring changed UI bytes.
4. **Expose an explicit opt-in CLI route.** Add paired state/configuration inputs
   only after cutover manifests prove stage hits, publication validation and atomic
   replacement. Until then, retain ordinary `satn compile` as the fallback and fail
   closed on missing or stale state (ADR 0015 [lines 183–205](../adr/0015-local-evidence-store-cli-and-lifecycle.md#L183-L205)).

## Verification performed

- `gh issue view 339 --repo awjreynolds/agentic-satn-compiler --json ...` — confirmed
  the ticket’s scope and no comments.
- `git show 405a4dd` — confirmed the Scenario Iteration coordinator was added as a
  standalone module plus focused tests.
- `git show 416afcb` — confirmed the review-lens UI changed the checked assets.
- `rg` symbol searches and source inspection — confirmed no production caller or
  CLI route for `iterate_scenario`, and no stage/assembly tables in the store schema.
- Focused tests: `tests/test_scenario_iteration.py` and the presentation-only
  regression in `tests/test_recompilation.py` pass in the repository environment.
