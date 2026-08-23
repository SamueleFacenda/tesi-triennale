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
kbench thesis  --run-dir runs/big                    # charts + LaTeX for thesis/
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

`colosseo` ships only as Turtle, which owlready2 cannot read (it has no Turtle parser —
only N-Triples, RDF/XML and OWL/XML). Its `.nt` is converted once, alongside the source:

```bash
oxigraph convert -f ~/downloads/ontos/colosseo_3DGraph.ttl \
                 -t ~/downloads/ontos/colosseo_3DGraph.nt   # 4.9 GB -> 19.4 GB, ~10 min
```

Only owlready2 picks it up; every other engine still loads the Turtle. `santa_chiara` is
Turtle-only too and would need the same treatment before owlready2 can run it.

## Thesis output

`kbench thesis --run-dir runs/big` regenerates everything the thesis shows, straight from
`bench.db`:

- `thesis/images/plots/*.png` — one chart per query (engines grouped by dataset), one per
  engine (queries grouped by dataset), plus load time / disk size / peak RSS. Query times
  are medians **net of that engine's `__baseline__`**, on a log axis.
- `thesis/generated/results_figures.tex` — the figure floats, two panels each, `\input` by
  `chapters/benchmark.tex`.
- `thesis/generated/results_tables.tex` — the appendix tables (raw medians, means, CV,
  min/max, row counts, plus the base-latency row), `\input` by `attachments/attachment.tex`.
- `thesis/generated/results.json` — the same grid, machine-readable.

Cells with no measurement are labelled rather than dropped: `TO` timed out, `n.d.` the
query does not apply to that dataset (gated by `Query.applies_to` at run time, or by
`APPLICABLE_ONLY` in `bench/thesis_export.py` at export time — currently empty, so every
query in `QUERY_ORDER` is reported on every dataset), `n.e.` was never executed (excluded
pair, failed load, or an interrupted run).

Both directories must be **committed**: `nix build .#thesis` builds from the git tree, so
untracked charts are invisible to it. To regenerate while a run is still in progress, copy
`bench.db` and `config.json` elsewhere and point `--run-dir` at the copy — `BenchStore`
opens the database read-write.

## Known limitations / notes

- **Serialization overhead** is measured primarily via the per-engine `__baseline__`
  query (a tiny 1-row query = the connection + serialization floor). The `server_s`
  column (server-reported execution time) is only populated when an engine returns it in
  standard SPARQL-JSON; none of the current engines do, so subtract the baseline median
  from a query's median for a pure-execution estimate.
- **Result correctness across engines is not yet cross-checked.** The harness records the
  row count per engine; some engines legitimately differ on the same query (e.g. Virtuoso
  returns fewer rows on the property-path queries, `chained_segmentation` and
  `taxonomical_hierarchy`). Differences are recorded, not hidden — a row-count/hash
  cross-check is a natural next step. (Queries are kept to portable SPARQL 1.1: e.g.
  `taxonomical_hierarchy` projects its `COUNT` as `?depth` and orders by the alias, since
  a bare aggregate in `ORDER BY` is rejected by qEndpoint/GraphDB/Virtuoso.)
- **Virtuoso truncates a single response at 2^20 = 1048576 rows** — with HTTP 200 and no
  warning, so a truncated answer is indistinguishable from a complete one. Its queries are
  therefore fetched in `OFFSET`/`LIMIT` pages of 1M rows (`VirtuosoEngine.chunk_rows`,
  same fix as the 3dont viewer) and the pages are summed into one measurement, so its
  timings for large results include the paging round-trips. The per-query timeout is the
  budget for the whole paged result, not per page.
- **Virtuoso's tuning lives in `virtuoso.ini`, which the image writes only once** — on
  the first boot into an empty `database/` dir, from the `VIRT_Parameters_*` env. A resumed
  run therefore served with the ini from its first load and silently ignored every setting
  added since: that is why `MaxSortedTopRows` never applied and the paged
  `taxonomical_hierarchy` failed with `SR353` (the `LIMIT` *value* is checked against it,
  not the result size, so even a 55-row answer fails at `LIMIT 1000000`).
  `VirtuosoEngine._patch_ini` now rewrites those keys in place before every start, from a
  throwaway root container.
- **A server can outlive `stop()`.** A launcher that does not `exec` its server
  (`qendpoint.sh`) is killed by the SIGTERM while the JVM it started is still running its
  shutdown hook — and still listening. On qEndpoint's fixed port 1234 the next dataset's
  health probe was then answered by that dying server, after which every query of that
  dataset failed with `connection refused`. `stop()` now returns only once the port is
  actually free (SIGKILLing the leftover process group after a grace period), and `start()`
  refuses a fixed port held by someone else rather than measuring their server.
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
  thesis_export.py  full result grid (missing cells explained) for the thesis
  thesis_charts.py  matplotlib PNG charts
  thesis_tables.py  generated LaTeX (figure floats + appendix tables)
  tui.py          rich progress + plain fallback
nix/              custom derivations (qendpoint, qlever-control) copied from 3dont
```
