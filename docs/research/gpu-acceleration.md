# GPU acceleration in the local SATN pipeline — 2026-07-28

## Decision

**Do not add GPU acceleration to SATN's local pipeline.** Do not add
RAPIDS/cuSpatial/cuGraph, a Metal-specific replacement, a container, or a GPU
daemon as an optional execution path for the present single-user,
disconnected-capable council workflow.

The only plausible future GPU candidate is the **Cross-Spine weighted
shortest-path batch**, not the whole compiler. Reconsider it only if the
product target becomes a supported NVIDIA/CUDA Linux workstation and profiling
shows that one stage dominates a regional compile after the CPU actions below.
It is not viable on the declared macOS/Apple Silicon target and is not justified
by the measured B&NES workload.

This is an architecture decision based on the facts and evidence below, not an
implementation proposal.

## Constraints and success budgets

The architecture is a local command-line compiler: no daemon, container,
administrator setup, or hosted UK-wide platform. A future council addition must
still work disconnected from a service.

| Operation | Budget | Current evidence | Status |
| --- | ---: | ---: | --- |
| B&NES cold compile | <= 120 s | 87.31 s | passes |
| Scenario iteration (unchanged inputs) | <= 60 s | validated whole-publication reuse: 4.70 s | passes for this use case |
| Spatial subset | <= 2 s | no representative end-to-end measurement | open |
| WECA cold compile | <= 600 s | an incomplete historical run had only reached publication at 4,801.6 s; final elapsed time is unknown | fails the budget as a measurement target; not a completed result |

The B&NES evidence records 41,158 road edges, while the incomplete WECA slice
loaded 256,327 edges. The large-scale result is deliberately not treated as a
completed compiler benchmark: it did not complete atomic publication and
omitted several governed evidence families. See [B&NES performance
evidence](banes-compile-performance-2026-07-28.md) and [WECA scale
evidence](weca-scale-benchmark-2026-07-27.md).

## Facts

### Present system and workload

- The target machine observed for this decision is a 48-GB Apple M3 Max
  MacBook Pro: 16 CPU cores, a 40-core integrated Apple GPU and Metal support.
  It has **no NVIDIA CUDA device**. (Read-only local hardware inspection on
  2026-07-28.)
- SATN's runtime dependencies are GeoPandas, Shapely/GEOS, Pyogrio/GDAL and
  NetworkX. The compiler principally works with GeoDataFrames, Shapely
  geometries and NetworkX graphs, and publishes several GeoJSON/GeoPackage
  artifacts. These are CPU-host object/data structures, not a GPU-resident
  columnar graph pipeline.
- The recent B&NES optimization already removed the two largest identified
  repeated geometric costs: it builds the NCN corridor once, selects road
  candidates through a spatial index, runs the unchanged overlap predicate in
  one vectorized operation, and transforms elevation evidence once per profile
  build. Cold time fell from 281.45 s to 87.31 s with an identical canonical
  published GeoJSON digest. This directly demonstrates that reducing repeated
  work produced the required outcome without a new compute platform.
- Remaining coarse B&NES cold phases are 22.9 s for combined post-Cross-Spine/
  access/topography/finalization, 20.4 s for publication, 17.7 s for backbone
  assembly and 2.9 s for snapshot load. They are coarse wall-clock groups, so
  none identifies a GPU kernel or a GPU break-even workload.

### GPU stack compatibility

- RAPIDS requires an NVIDIA Volta-or-newer GPU (compute capability 7.0+), CUDA,
  and supported Linux or Windows through WSL2; its supported-platform list does
  not include macOS/Apple GPUs. [RAPIDS installation
  requirements](https://docs.rapids.ai/install/) and [platform
  support](https://docs.rapids.ai/platform-support/) are the owning project
  documentation.
- cuSpatial provides GPU spatial indexing, joins and geometry operations.
  However, its GeoPandas bridge explicitly copies a host GeoPandas frame into
  GPU memory with `cuspatial.from_geopandas`. [cuSpatial
  overview](https://docs.rapids.ai/api/cuspatial/stable/) and [cuSpatial I/O
  API](https://docs.rapids.ai/api/cuspatial/stable/api_docs/io/) document both
  capability and conversion boundary. Its examples also constrain several
  operations to homogeneous geometry types, unlike SATN's rich heterogeneous
  geometry/dataframe and provenance structures.
- cuGraph's `nx-cugraph` is an optional NetworkX backend for many, not all,
  NetworkX algorithms, and is explicitly NVIDIA/RAPIDS acceleration. [NetworkX
  backends](https://networkx.org/documentation/stable/backends.html) and
  [nx-cugraph](https://docs.rapids.ai/api/cugraph/stable/nx_cugraph/) are the
  primary documentation. NetworkX documents that dispatch can convert and cache
  input graphs and fallback can convert them back; these are potentially
  expensive. Fallback is opt-in and unsupported backend calls otherwise raise.
  [NetworkX backend dispatch](https://networkx.org/documentation/latest/reference/backends.html).
- Apple Metal is available for custom compute and framework-specific paths such
  as PyTorch MPS, but those documents do not provide a Metal execution backend
  for RAPIDS/cuSpatial/cuGraph, GeoPandas or GEOS. [Apple Metal
  overview](https://developer.apple.com/metal/) and [PyTorch MPS on
  Mac](https://developer.apple.com/metal/pytorch/) establish the available
  platform, not a compatible SATN geospatial stack.

### CPU paths compatible with the current pipeline

- Shapely wraps GEOS and exposes NumPy-style vectorized ufuncs; the C loops
  reduce Python-loop overhead, and Shapely generally releases the GIL during
  operations. [Shapely 2 documentation](https://shapely.readthedocs.io/en/stable/)
  supports the existing vectorized-CPU and controlled-parallelism direction
  without changing geometry semantics or converting to another engine.
- Pyogrio is bulk-oriented OGR I/O. With GDAL >= 3.6 and PyArrow installed,
  `use_arrow=True` uses GDAL's Arrow stream and can be faster for large files;
  it retains the GeoPandas result boundary. [Pyogrio
  API](https://pyogrio.readthedocs.io/en/latest/api.html) and [how Pyogrio
  works](https://pyogrio.readthedocs.io/en/latest/about.html) are the primary
  sources. This is a small, local optional dependency rather than a GPU
  runtime; benchmark it before adding it because filtering/slicing can have
  Arrow batch overhead.
- Apache Arrow's columnar format is scan- and SIMD-friendly and permits
  zero-copy access in shared memory. It is a useful interchange format for
  selected tabular work, not a way to make GEOS or NetworkX GPU-resident.
  [Arrow columnar format](https://arrow.apache.org/docs/format/Columnar.html).
- DuckDB Spatial offers a local R-tree index that prunes candidates before
  expensive exact predicates. It can accelerate a persistent, constant-window
  spatial subset, but its published R-tree limitations mean it is not a
  drop-in acceleration for arbitrary joins. [DuckDB spatial
  R-trees](https://duckdb.org/docs/stable/core_extensions/spatial/r-tree_indexes.html).
  Its extension must be installed/loaded, which is an operational choice to
  make only after a measured subset problem exists.
- Polars is a possible CPU-only option for purely tabular, non-geometry
  transforms: its lazy engine applies projection/predicate pushdown and its
  execution uses available CPU cores. It would add conversion at GeoPandas/
  Shapely boundaries, so it should not replace the spatial pipeline by default.
  [Polars lazy optimization](https://docs.pola.rs/user-guide/lazy/optimizations/)
  and [parallel execution](https://docs.pola.rs/).

## Inference and trade-off assessment

| Concern | GPU consequence | Decision implication |
| --- | --- | --- |
| Installation and operations | RAPIDS needs NVIDIA/CUDA and supported Linux/WSL; a reproducible local path would require a separate hardware/runtime environment, ordinarily Conda or Docker. | Violates the no-container/no-admin, Mac-first local constraint. |
| Data conversion and transfer | cuSpatial begins by copying GeoPandas data to device; NetworkX dispatch can convert/cache graphs, then SATN publication needs host GeoPandas/Shapely data again. | At B&NES scale, conversion/serialization and join boundaries would consume the time a kernel might save. |
| Compatibility | GEOS/Shapely operations, CRS transforms, Pyogrio/GDAL I/O, arbitrary attributes/provenance and the full NetworkX algorithm surface have no single compatible GPU replacement. | A partial backend would create mixed CPU/GPU fallbacks and duplicate correctness paths. |
| Determinism and governance | SATN compares canonical geometry/order/provenance and validates whole publications. GPU parallel reductions, graph ordering and backend fallback need separate exact-output proof, not merely a speed claim. | Validation burden is materially higher than a CPU indexing/vectorization change. |
| Current measured benefit | B&NES cold and reuse budgets already pass after spatial indexing and removal of repeated projection; publication is I/O/serialization-heavy. | No named B&NES GPU-worthy stage or measured break-even exists. |
| WECA gap | The incomplete WECA run is far above the new 10-minute budget, but has no per-stage profile and cannot show whether geometry, graph search, parsing or publication dominates. | Profile and remove repeated CPU/I/O work first; choosing GPU now is speculative. |

Integrated memory on Apple Silicon may reduce one class of physical-copy cost for
Metal programs, but it does **not** supply CUDA or turn GeoPandas, GEOS, GDAL,
or NetworkX objects into Metal inputs. It therefore does not change the
compatibility conclusion.

## CPU-first plan and measurement gates

1. **Make the 2-second spatial-subset operation explicit and benchmark it.**
   Record dataset, CRS, predicate, result cardinality, cold/warm cache state,
   p50/p95 wall time and an exact result fingerprint. First use existing
   GeoPandas spatial indexes and Shapely vectorized predicates. If a persistent
   constant-window lookup still misses 2 seconds, prototype a *local* DuckDB
   Spatial R-tree behind a narrow read-only adapter and compare result identity
   plus end-to-end time, including conversion.
2. **Profile a complete WECA cold compile before changing technologies.**
   Preserve the atomic completion gate; time source parsing, CRS conversion,
   spatial-index creation/query, GEOS operations, graph construction/search and
   each publication writer separately. Capture CPU time, RSS and input/output
   sizes. The present partial heartbeat is not a profile.
3. **Eliminate repeated CPU work in the measured hot phase.** Reuse per-run
   projected frames and spatial indexes; filter before parsing where the source
   format permits it; use Pyogrio Arrow I/O only if it improves the governed
   end-to-end benchmark; retain whole-publication validation/reuse. Apply
   Shapely ufuncs instead of Python `iterrows` loops where exact result
   semantics are covered by tests. Consider Polars/Arrow only for a measured
   pure-table segment, not geometry or graph authority.
4. **Treat graph search separately.** The Cross-Spine code already records
   searches, settled nodes, edge relaxations and peak frontier. Use those
   counters plus timings to decide whether bounded source-group shortest-path
   search, graph assembly, or another stage is responsible. A CPU graph
   representation/algorithm improvement that preserves route tie-breaking and
   canonical output comes before a new backend.

## Reconsideration threshold (not currently met)

A GPU feasibility spike is permitted only after all of the following are true:

- the supported product target is explicitly an NVIDIA CUDA Linux workstation,
  not an Apple Silicon Mac, and the owner accepts its install/support burden;
- a completed, governed regional benchmark shows the **Cross-Spine weighted
  shortest-path batch** alone is at least 120 seconds of the cold run *or* at
  least 30 seconds of a changed-input scenario iteration after the CPU-first
  changes, with fixed input digests and recorded work counters;
- a three-or-more-run comparison includes graph/data conversion, device
  transfer, fallback and final host-side publication. It must give at least a
  2x end-to-end reduction for that stage and move the complete regional run
  toward <= 600 seconds; and
- GPU and CPU-fallback runs produce byte-identical governed artifacts, or an
  explicitly approved canonical equivalence proof, identical decisions and
  diagnostics, and fall back cleanly with no daemon or network requirement.

This makes the potential cuGraph path measurable while preventing a
hardware-driven architecture from being introduced on a hunch. cuSpatial is not
a candidate until a separate profile identifies a single homogeneous, large
spatial batch whose conversion-inclusive CPU baseline fails its budget.

## Open questions

- What is the exact user-facing subset query and its cold/warm 2-second
  acceptance measurement?
- Which named stages dominate a **completed** WECA full cold compile using the
  required governed evidence? The current evidence cannot answer this.
- Can a local GeoPackage/Parquet/Arrow cache be added without weakening the
  existing input-digest, dependency-fingerprint and atomic-publication
  contracts? This is a CPU/cache design question, not authorization for a
  cache.
- If a future council supplies an NVIDIA Linux workstation, do regional
  profiles cross the stated Cross-Spine break-even threshold?

## Conclusion

GPU acceleration is ruled out for the current local SATN architecture. It is
incompatible with the target M3/Metal machine through the relevant RAPIDS
stacks, would add unsupported conversion/fallback/determinism paths to the
GEOS/Shapely/Pyogrio/NetworkX pipeline, and has no measured B&NES stage to repay
that cost. The immediate route to the budgets is evidence-led: spatial indexes,
vectorized GEOS/CPU work, cached/validated whole publications, and removal of
repeated parsing, projection and serialization. Only a profiled,
NVIDIA-supported future regional Cross-Spine batch may reopen the question
under the explicit break-even gate above.
