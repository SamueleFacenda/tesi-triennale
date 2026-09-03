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
datasets    = ["mini_heritage", "mini_urban"]   # the big graphs are opt-in
queries     = "all"                              # or a list of query names

# Force one SPARQL result serialization for every engine, for a uniform control run.
# When unset, each engine is queried in the format it emits fastest (see measurements.md).
# result_format = "json"    # json | xml | csv | tsv

# Engine/dataset pairs to skip, for pairs that are unaffordable rather than merely slow:
# a load that gets OOM-killed on every resume, a store that would not fit on disk. Must be
# the last table in the file. Everything not listed still runs.
[exclude]
rdflib = ["grande"]
```

A dataset name is one logical graph (`ytu3d`, `torre_modena`, and so on), not one file: each engine
picks a serialization it can read. `kbench list-datasets` shows what is present locally.
Unknown keys, engines, datasets or queries are rejected at load time, not mid-run.

## Memory / tuning

Engines are configured for large datasets, not run on defaults. A single memory **budget**
is derived per engine. One engine runs at a time, so each may use it all. It is
`mem_fraction` of system RAM (default `0.5`; every sample config raises it to `0.75`) or an
explicit `memory_gb`, minus ~25% of RAM always reserved for native/off-heap allocations.

| Engine    | Applied tuning |
|-----------|----------------|
| fuseki    | `JVM_ARGS=-Xmx<budget>g` on tdb2 loader + server |
| qendpoint | `-Xmx<budget>g`; HDT built with all 6 permutations, on-disk cat-tree loader, parallel compression |
| qlever    | `STXXL_MEMORY` + `PARALLEL_PARSING` (sized to the input's longest line) for indexing, `MEMORY_FOR_QUERIES` for serving |
| virtuoso  | `NumberOfBuffers` / `MaxDirtyBuffers` sized to RAM (~85k buffers/GB), `MaxQueryMem`, threads |
| graphdb   | `GDB_HEAP_SIZE=<budget>g` |
| stardog   | heap + `MaxDirectMemorySize` split evenly |

The engines not listed get no tuning: oxigraph's RocksDB self-manages its cache, and rdfox,
rdflib, rdflib-bdb and owlready2 take no memory knob from the harness.

qLever's result cache is pinned to one byte, which switches it off, so its measured
repetitions are executions instead of cache lookups. [notes.md](notes.md) has the numbers.

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
