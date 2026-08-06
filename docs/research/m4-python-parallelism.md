# Python execution models on Apple Silicon — 2026-08-05

Research for [#346 — benchmark Python execution models on Apple M4](https://github.com/awjreynolds/agentic-satn-compiler/issues/346).

## Decision-oriented conclusion

Adopt a **benchmark-driven concurrency contract**, not an M4-specific worker
count. The portable default is one worker with canonical, deterministic output.
Use a thread pool only around measured native work that releases the GIL (for
example, Shapely vectorized GEOS operations). Use a process pool only for coarse,
CPU-heavy Python work whose arguments and results are small, picklable partition
records; choose an explicit `spawn` context on macOS. Let DuckDB own parallelism
for a query, or run several DuckDB queries with one internal thread each. Do not
multiply outer workers by DuckDB threads without a benchmark and memory budget.

Every candidate must pass a one-worker versus many-worker equivalence check and
record wall time, CPU time, peak RSS, serialized bytes, worker-start overhead,
failure/cancellation behaviour, and output digest. M4 is a useful reference
machine, not an authority for a fixed `max_workers`.

## Scope and reference machines

The repository requires Python `>=3.12` and currently uses GeoPandas, Shapely,
Pyogrio/GDAL, NetworkX and DuckDB. The [parallel-reduction runtime](../../src/satn/parallel_reduction.py)
creates a one-thread executor only to impose a timeout around an optional
provider; this is isolation, not evidence that the compiler should parallelize.

Apple's M4 MacBook Air specification lists a 10-core CPU (4 performance and 6
efficiency cores), unified memory, and 120 GB/s memory bandwidth
([Apple M4 technical specifications](https://support.apple.com/en-us/122209)).
The local read-only inspection for this note is **not M4**: MacBook Pro, Apple M3
Max, 12 performance plus 4 efficiency cores, 48 GB RAM, macOS 26.5.2 arm64,
Python 3.12.13, DuckDB 1.4.4, Shapely 2.1.2, NumPy 2.5.1. No conclusion below
claims that these M3 timings predict an M4 result.

## What the primary sources establish

| Model | Verified fact | Practical consequence |
| --- | --- | --- |
| Python threads | CPython's GIL permits only one thread to execute Python objects at a time; extension code can release it around blocking/native work ([CPython C API](https://docs.python.org/3.12/c-api/init.html#thread-state-and-the-global-interpreter-lock)). `ThreadPoolExecutor` is still useful for I/O and for CPU work that releases the GIL; its default worker count is an intentionally conservative heuristic, not a hardware contract ([Python 3.12 `concurrent.futures`](https://docs.python.org/3.12/library/concurrent.futures.html#threadpoolexecutor)). | Pure Python loops should remain sequential or move to processes. Native calls need a measured thread count and must avoid unsafe shared mutable state. |
| Python processes | `ProcessPoolExecutor` bypasses the GIL but only accepts picklable callables/arguments/results. `map` can batch process work with `chunksize`; an initializer can load immutable state ([Python 3.12 `ProcessPoolExecutor`](https://docs.python.org/3.12/library/concurrent.futures.html#processpoolexecutor)). | Send stable IDs, paths, or compact arrays—not GeoDataFrames or large object graphs. Batch enough work to amortize startup and serialization. |
| macOS start method | On macOS, `spawn` is the default from Python 3.8; `fork` is considered unsafe because system libraries may have started threads ([Python multiprocessing contexts](https://docs.python.org/3.12/library/multiprocessing.html#contexts-and-start-methods)). | Pass an explicit context to a library boundary; keep worker functions importable and protect entry points with `if __name__ == '__main__'`. Do not rely on inherited interpreter state. |
| Inter-process memory | `multiprocessing.shared_memory` can avoid serializing/copying large numeric buffers, but it requires explicit `close()`/`unlink()` lifecycle management ([Python shared memory](https://docs.python.org/3.12/library/multiprocessing.shared_memory.html)). | Consider shared memory or Arrow only after measuring RSS and copy cost. It does not make Python object graphs or GEOS objects safely shared. |
| Native geometry | Shapely 2 exposes vectorized NumPy/GEOS operations and generally releases the GIL while GEOS does the heavy work ([Shapely 2.x release notes](https://shapely.readthedocs.io/en/2.0.0/release/2.x.html#releasing-the-gil-for-multithreaded-applications)). | A thread pool is a credible candidate for independent vectorized geometry batches; retain canonical ordering and test exact results. |
| DuckDB | `threads`/`worker_threads` defaults to the number of CPU cores and can be set with `SET threads = ...` ([DuckDB configuration](https://duckdb.org/docs/stable/configuration/overview)). A DuckDB connection is not thread-safe; each Python thread needs its own connection (or thread-local cursor as documented) ([DuckDB Python API](https://duckdb.org/docs/stable/clients/python/overview), [multiple Python threads](https://duckdb.org/docs/current/guides/python/multiple_threads)). | Pick one parallelism owner: one query with bounded DuckDB threads, or many independent queries with `threads=1` (or a measured small value). Never share the global `duckdb.sql()` connection. |
| Determinism | SQL set semantics and some floating-point aggregates can be non-deterministic under parallel execution. DuckDB recommends `SET threads = 1` and explicit ordering such as `ORDER BY ALL` where deterministic output is required ([DuckDB non-deterministic behaviour](https://duckdb.org/docs/current/operations_manual/non-deterministic_behavior)). | Sort records by stable IDs and tie-breaks before hashing/publication; use a one-thread reference run for equivalence. |
| Failure and cancellation | `Future.cancel()` only cancels work that has not started; running work continues. Executor shutdown waits for pending work by default ([Python executor shutdown](https://docs.python.org/3.12/library/concurrent.futures.html#concurrent.futures.Executor.shutdown)). Abrupt process failure raises `BrokenProcessPool`; terminating a process using a pipe/lock can corrupt it ([Python multiprocessing](https://docs.python.org/3.12/library/multiprocessing.html#process-objects)). | Use cooperative deadlines, bounded queues and fail-closed publication. Do not treat cancellation as rollback or kill a process while it owns shared synchronization state. |

## Local microbenchmarks (directional M3 evidence only)

These are small synthetic checks, not compiler benchmarks. They ran from the
checked-in `.venv` with no repository files changed. Each case used four tasks.

| Case | 1 worker / sequential | 2 workers | 4 workers | 8 workers | Observation |
| --- | ---: | ---: | ---: | ---: | --- |
| Pure Python integer loop | 0.156 s | 0.160 s (threads) | 0.159 s | 0.158 s | Threads did not improve GIL-bound bytecode. |
| Shapely vectorized `buffer` (120,000 points/task) | 1.379 s | 1.572 s (threads) | 0.979 s | 1.001 s | Four threads helped this native workload in one run; eight did not. This is a tuning signal, not a default. |
| 4 MiB payload to fresh Python subprocesses | 0.034 s (one, serial) | — | 0.021 s (four concurrent) | 0.025 s (eight concurrent) | Process startup and copying are measurable even when the child does trivial work; this is not a `ProcessPoolExecutor` result. |
| DuckDB `sum(i*i)` over 30M generated rows, independent connections | 0.074 s (`threads=1`) | 0.080 s (two outer, `threads=2`) | 0.085 s (four outer, `threads=4`) | — | This synthetic query was too small to expose useful scaling; all results were equal. Benchmark real scans/joins, not `range()` alone. |

The process-pool portion could not run in this restricted macOS environment:
`ProcessPoolExecutor` failed before worker creation with
`PermissionError: [Errno 1] Operation not permitted` while checking
`SC_SEM_NSEMS_MAX`. This is an environment blocker, not evidence that process
parallelism is unsuitable. A normal M4 host must rerun the process matrix.

The Shapely follow-up compared sequential and 2/4/8-thread outputs for 20,000
geometries per task; raw WKB arrays and normalized WKB arrays were equal for all
tasks. The check is deliberately small: the acceptance contract still requires
canonical logical digests on representative SATN partitions.

Commands used for local evidence:

```console
/private/tmp/satn-publish-review-lens/.venv/bin/python -c 'import sys,platform,duckdb,shapely,numpy; print(sys.version, platform.platform(), duckdb.__version__, shapely.__version__, numpy.__version__)'
system_profiler SPHardwareDataType
```

The microbenchmarks were inline `concurrent.futures`, Shapely, DuckDB and
`subprocess` scripts executed with the same `.venv/bin/python`; no full SATN
compile was run.

## Portable concurrency contract

1. **Reference path.** Execute one worker, one DuckDB thread, stable input order,
   and canonicalize output by governed feature/edge identity. Store its logical
   digest, diagnostics and counts as the equivalence reference.
2. **Work unit.** Partition by stable source/edge IDs (or the existing stable
   geographic partition contract), not by row order or worker ID. A worker may
   read immutable halo/context data but publishes only owned records. Return
   compact records or file references; never merge arbitrary mutable GeoPandas or
   NetworkX objects through a queue.
3. **Threads.** Permit threads only when profiling shows native/GIL-releasing work
   dominates. Give each thread independent scratch state and deterministic output
   slots. For DuckDB, use a separate connection per thread; set internal threads
   so `outer_threads × inner_threads` fits the measured CPU/RSS budget.
4. **Processes.** On macOS, use an explicit `spawn` context and top-level worker
   functions. Load large immutable inputs once per worker through an initializer
   or a read-only path; pass partition IDs and compact parameters. Use `chunksize`
   for many tiny tasks. A process count is selected by the benchmark, bounded by
   memory, and may be lower than visible CPU count.
5. **Mixed execution.** Do not nest a process pool around a fully threaded DuckDB
   query or a threaded native library unless the benchmark explicitly includes
   the product of both levels. Set inner native/DuckDB threads to one as the
   starting point for process workers.
6. **P/E cores and memory.** Treat `os.cpu_count()` as an upper bound only; do not
   assume a stable P/E-core API or hard-code “10 workers for M4”. Sweep a small
   portable set derived from the host (one, two, then increasing values up to the
   measured knee). Record parent and child peak RSS: unified memory is shared
   physical memory, but process heaps, decoded geometries and serialization still
   create pressure and may evict useful caches.
7. **Failure/cancellation.** Workers report typed failures with partition IDs.
   Stop scheduling new work after a deadline, let running native calls reach a
   safe boundary, and leave the last validated publication intact. A partial
   worker result is never silently accepted as a complete publication.
8. **Equivalence gate.** Run the same partition set at one worker and at each
   candidate setting. Compare canonical feature/edge digests, decisions,
   provenance references, gap diagnostics, counts and ordering—not only elapsed
   time. For DuckDB, use explicit `ORDER BY` and a one-thread reference where
   floating-point aggregates or set ordering participate.

## Benchmark protocol for an M4 host

Use a checked-in synthetic fixture plus one representative SATN partition. Warm
and cold runs are separate. For each model (sequential, threads, spawned
processes, DuckDB-only, mixed), sweep worker counts derived from the host and
run at least three repetitions after one warm-up. Record:

- wall-clock p50/p95, CPU time and task-size distribution;
- process/thread startup time, serialized input/output bytes and chunksize;
- parent and per-worker peak RSS, shared-memory lifetime and temporary files;
- CPU utilisation and whether the run is bounded by P or E cores;
- first failure, timeout, cancellation latency and publication state; and
- exact logical output digest plus a diff of any diagnostics.

Choose the fastest setting that passes equivalence and memory limits with a
material margin. Re-run after Python, GEOS, GDAL, DuckDB, OS or hardware changes;
the contract is portable because the benchmark selects the setting, while the
one-worker path remains the deterministic fallback.

## Open blockers

- No M4 host was available for this note; process-pool timing and P/E-core
  behaviour require a normal M4 run outside the restricted environment.
- The synthetic DuckDB query did not represent SATN scans, spatial predicates or
  joins. A real benchmark must include the governed evidence-store shape and
  conversion/publication costs.
- The current source has no process-safe serialization contract for full
  GeoDataFrames, Shapely object arrays or NetworkX graphs. Designing one is a
  follow-up implementation task, not authorization to parallelize the compiler.
