# kbench — knowledge-store benchmark harness

Automatic, resumable benchmark that runs the same SPARQL queries across many RDF /
knowledge-store engines and datasets, over a uniform HTTP interface, and writes
machine-readable results.

It is the benchmarking companion to the [3dont](../3dont) viewer: the query set and the
per-dataset ontology namespaces come from there.

## Quick start

```bash
nix develop            # or: direnv allow
kbench list-engines    # what can run here
kbench list-datasets   # what data is present locally
kbench list-queries

kbench run --config bench.toml          # run the benchmark
kbench status  --run-dir runs/default   # progress so far
kbench resume  --run-dir runs/default   # continue after Ctrl-C
kbench report  --run-dir runs/default --format csv   # export results
```

(`kbench` is `python -m bench`; both work inside the dev shell.)

## What it measures

Every engine is normalised to an **HTTP SPARQL endpoint**, so the client-side query path
(send → execute → receive → parse) is identical and comparable. Recorded per run:

- **Query latency** — wall-clock over N repetitions (default 10) with warmup runs
  discarded; the report gives min / median / mean / p95 / stddev.
- **Load time & disk** — bulk-ingest time and on-disk size of each engine's store.
- **Peak RSS** — sampled from the server process (native) or `docker stats` (containers).
- **Serialization overhead** — a per-engine baseline (tiny 1-row query) plus, where the
  engine reports it, server-side execution time; `wall − server` estimates transport +
  (de)serialization.

Runs are strictly **sequential** (one engine up, one query at a time) so each query gets
the machine's full resources with no cross-query interference.

## Configuration (`bench.toml`)

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

## Result format

The SPARQL **result serialization** strongly affects latency, so each engine is queried
in the format it emits fastest (measured on a 15 000-row result; medians):

| Engine | json | tsv | csv | used |
|---|--:|--:|--:|---|
| oxigraph | 107 | 48 | 48 | **tsv** |
| fuseki | 255 | **43** | 45 | **tsv** |
| qendpoint | 125 | 89 | 86 | **tsv** |
| qlever | 150 | 46 | 44 | **tsv** |
| virtuoso | 203 | 74 | 74 | **tsv** |
| graphdb | 171 | 84 | **46** | **csv** |

JSON is the slowest everywhere (2–6×); TSV is the default (fast, and its line-count parse
can't be fooled by embedded newlines); GraphDB overrides to CSV (its TSV is ~1.8× slower).
The client reads the whole body (transfer is timed) and counts rows with an O(n) method
that costs the same for every engine, so differences reflect the engine's serialization —
row counts are identical across formats (verified). Set `result_format = "json"` (or any
of `json/xml/csv/tsv`) in `bench.toml` to force one format for a uniform control run.

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

## Engines

| Engine    | Packaging            | Notes |
|-----------|----------------------|-------|
| oxigraph  | native (nix)         | built-in HTTP server |
| fuseki    | native (nix)         | TDB2 + Fuseki server |
| qendpoint | native (nix)         | HDT store, Spring endpoint (fixed port 1234) |
| qlever    | native bins or docker| Turtle/N-Triples only; via `qlever` control CLI |
| virtuoso  | docker               | not in nixpkgs; loads into a named graph |
| graphdb   | docker (Free)        | no license needed |
| rdfox     | docker + license     | set `$RDFOX_LICENSE` |
| stardog   | docker + license     | set `$STARDOG_LICENSE` |
| rdflib    | native (embedded)    | in-process lib wrapped in a tiny HTTP server; parsed graph persisted as N-Triples |
| owlready2 | native (embedded)    | in-process lib wrapped in HTTP; SQLite "world" persistence; reads RDF/XML & N-Triples (not Turtle); needs real OWL data — its SPARQL is a subset and errors on some queries |

Docker engines are launched through the system `docker`/`podman`; they report themselves
unavailable (and are skipped cleanly) when the daemon or a required license is missing.

### Adding an engine

Subclass `AbstractEngine` (or `DockerEngine`) in `bench/engines/`, implement
`load` / `_start` / `endpoint_url`, and decorate with `@register_engine("name")`. Import
it in `bench/engines/__init__.py`. Querying is inherited.

## Queries and datasets

Queries are templates in `bench/queries/builtin.py`, parameterised by the dataset's base
ontology namespace and read from the default graph (no `FROM`). Add one with
`@register_query`.

A **dataset is one logical graph** (`bench/datasets.py`) that may exist in several
serializations — `nettuno.nt`, `nettuno.rdf` are the *same* graph, not two datasets. Each
engine auto-picks a serialization it supports (Turtle preferred; QLever/RDFox can't read
RDF/XML), and the chosen format is recorded per load. The big source files under
`~/downloads/ontos` are referenced by path, never copied into the repo.

**Fast complete test:** `mini_heritage` / `mini_urban` are small real-shaped samples
(a `head` of the big `.nt` files, converted to all three formats) under a gitignored
`testdata/`. Regenerate them with:

```bash
head -n 15000 ~/downloads/ontos/torre_modena.nt > testdata/mini_heritage.nt
oxigraph convert -f testdata/mini_heritage.nt -t testdata/mini_heritage.ttl
oxigraph convert -f testdata/mini_heritage.nt -t testdata/mini_heritage.rdf --to-format application/rdf+xml
```

The committed `tiny` fixture (synthetic, all three formats) is for smoke tests.

## Known limitations / notes

- **Serialization overhead** is measured primarily via the per-engine `__baseline__`
  query (a tiny 1-row query = the connection + serialization floor). The `server_s`
  column (server-reported execution time) is only populated when an engine returns it in
  standard SPARQL-JSON; none of the current engines do, so subtract the baseline median
  from a query's median for a pure-execution estimate.
- **Result correctness across engines is not yet cross-checked.** The harness records the
  row count per engine; some engines legitimately differ on the same query (e.g. Virtuoso
  returned fewer rows on the property-path queries). Differences are recorded, not hidden
  — a row-count/hash cross-check is a natural next step. (Queries are kept to portable
  SPARQL 1.1: e.g. `taxonomical_hierarchy` projects its `COUNT` as `?depth` and orders by
  the alias, since a bare aggregate in `ORDER BY` is rejected by qEndpoint/GraphDB/Virtuoso.)
- **Docker engines write root-owned files**; `--fresh` and re-load remove them via a
  throwaway `busybox` container. Docker RSS is read from `docker stats`.
- **Loader output is logged** to `runs/<run>/storage/<engine>/<engine>.log` (native
  loaders stream there; container logs are appended before teardown), so a failed or
  OOM-killed load leaves a diagnosable trace. Signal deaths (e.g. SIGKILL from the OOM
  killer) are reported explicitly. If a big load is killed, lower `mem_fraction` /
  `memory_gb` — the budget already reserves ~25% of RAM for native/off-heap use.
- qLever in docker mode: RSS sampling reflects the `docker run` client, not the container
  (accurate only with native qLever binaries).

## Layout

```
bench/
  __main__.py     CLI
  config.py       BenchConfig (TOML)
  datasets.py     dataset descriptors
  queries/        query registry + builtins
  engines/        AbstractEngine, registry, http client, one module per engine
  metrics.py      timing, RSS sampler, disk usage
  store.py        SQLite persistence (resumable)
  runner.py       sequential orchestration
  report.py       CSV / JSON / parquet export
  tui.py          rich progress + plain fallback
nix/              custom derivations (qendpoint, qlever-control) copied from 3dont
```
