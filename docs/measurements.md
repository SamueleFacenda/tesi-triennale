# What it measures

How a measurement is produced, and what the numbers in a run mean.

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

---

[← back to the README](../README.md)
