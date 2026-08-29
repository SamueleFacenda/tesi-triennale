# Configuration

Everything a `bench.toml` can set, how memory is budgeted per engine, and how a run resumes.

## `bench.toml`

```toml
run_name    = "default"
repetitions = 10
warmup      = 2
timeout_s   = 300
output_dir  = "runs"
engines     = ["oxigraph", "fuseki", "qendpoint", "qlever"]
datasets    = ["torre_modena_ttl", "ytu3d_nt"]   # large files are opt-in
queries     = "all"                               # or a list of query names
```

## Memory / tuning

Engines are configured for large datasets, not run on defaults. A single memory
**budget** (default `mem_fraction = 0.75` of system RAM, or an explicit `memory_gb`) is
derived per engine — one engine runs at a time, so each may use it all:

| Engine    | Applied tuning |
|-----------|----------------|
| fuseki    | `JVM_ARGS=-Xmx<budget>g` on tdb2 loader + server |
| qendpoint | `-Xmx<budget>g`; HDT built with all 6 permutations, on-disk cat-tree loader, parallel compression |
| qlever    | `STXXL_MEMORY` + `PARALLEL_PARSING` for indexing, `MEMORY_FOR_QUERIES` + `CACHE_MAX_SIZE` for serving |
| virtuoso  | `NumberOfBuffers` / `MaxDirtyBuffers` sized to RAM (~85k buffers/GB), `MaxQueryMem`, threads |
| graphdb   | `GDB_HEAP_SIZE=<budget>g` |
| stardog   | heap + `MaxDirectMemorySize` split evenly |
| oxigraph  | none — RocksDB self-manages its cache |

## Resumability

Each run lives in `runs/<run_name>/`: a `config.json` snapshot, a `bench.db` SQLite
database (all measurements + progress), `storage/<engine>/<dataset>/` (each engine's
persisted store, loaded once), and per-engine logs. Every measurement is committed
immediately, so the run is safe to stop with Ctrl-C and continue with `kbench resume` —
already-completed measurements and already-loaded datasets are skipped. `--fresh` wipes.

A query that exceeds `timeout_s` is recorded with `status = "timeout"` (reported as
`runs_timeout` in `summary.csv`) and is not attempted again: neither its warmups nor its
remaining repetitions run, on this run or on any resume. Plain errors are still retried,
since they are cheap and often transient.

---

[← back to the README](../README.md)
