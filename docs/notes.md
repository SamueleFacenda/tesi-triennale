# Known limitations and findings

Behaviour worth knowing about before trusting a number, and the measurements behind the harness's less obvious choices.

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
  same fix as the 3DOnt viewer) and the pages are summed into one measurement, so its
  timings for large results include the paging round-trips. The per-query timeout is the
  budget for the whole paged result, not per page.
- **Virtuoso's tuning lives in `virtuoso.ini`, which the image writes only once** — on
  the first boot into an empty `database/` dir, from the `VIRT_Parameters_*` env. Passing
  the env alone would leave a resumed run serving with the ini its first load wrote,
  silently ignoring every setting added since, so `VirtuosoEngine._patch_ini` rewrites
  those keys in place before every start, from a throwaway root container. One of them has
  to be there for the paged queries to work at all: `MaxSortedTopRows` is checked against
  the `LIMIT` *value*, not against the number of rows that come back, so an ordered query
  fails with `SR353` at `LIMIT 1000000` even when its answer is 55 rows.
- **A server can outlive `stop()`.** A launcher that does not `exec` its server
  (`qendpoint.sh`) is killed by the SIGTERM while the JVM it started is still running its
  shutdown hook — and still listening. On qEndpoint's fixed port 1234 the next dataset's
  health probe is then answered by that dying server, and every query of that dataset
  fails with `connection refused` once it goes. So `stop()` returns only when the port is
  actually free (SIGKILLing the leftover process group after a grace period), and `start()`
  refuses a fixed port held by someone else instead of measuring their server.
- **qLever's result cache is switched off** (`--cache-max-size 1B
  --cache-max-size-single-entry 1B` on `qlever start`). It is the only engine here that
  memoizes whole query results, and the runner sends the identical text for the warmups and
  every repetition, so with the cache on the warmups pay for the computation and every
  measured rep is a lookup: server-side **0-1 ms against 13-65 ms cold** for `avg_height`,
  flat across datasets 15x apart in size. `--cache-max-num-entries 0` does *not* switch it
  off (QLever still keeps one entry, measured); a one-byte ceiling does. Its `--timeout` is
  also pinned to the harness budget, since qlever-control otherwise defaults the server to
  30 s and an over-budget query would return an error rather than a timeout.
- **`select_points_in_object` names its object outright** (`points_object` per dataset in
  `bench/datasets.py`). Choosing it in-query with `ORDER BY ?obj LIMIT 1` would order IRIs,
  which is implementation-defined, so each engine measures a *different* object: on grande
  qLever returns 9,686,001 rows where the other engines return 130. Each pinned IRI is the
  smallest in codepoint order, which is what 8 of the 10 engines pick on their own. A
  dataset with no object pinned skips the query (`applies_to`).
- **Disk usage excludes hardlinked files** (`dir_size`): qLever hardlinks the source dataset
  into its store as `input.nt`, which claims no blocks, so counting it would measure the
  dataset instead of the index (nettuno: 13.47 GB against 0.47 GB of actual index). A real
  copy such as rdflib's `graph.nt` has one link and still counts.
- **Docker engines write root-owned files**; `--fresh` and re-load remove them via a
  throwaway `busybox` container. Docker RSS is read from `docker stats`.
- **Loader output is logged** to `runs/<run>/storage/<engine>/<engine>.log` (native
  loaders stream there; container logs are appended before teardown), so a failed or
  OOM-killed load leaves a diagnosable trace. Signal deaths (e.g. SIGKILL from the OOM
  killer) are reported explicitly. If a big load is killed, lower `mem_fraction` /
  `memory_gb` — the budget already reserves ~25% of RAM for native/off-heap use.
- qLever in docker mode: RSS sampling reflects the `docker run` client, not the container
  (accurate only with native qLever binaries).

## SPARQL portability: Virtuoso and property paths

Two of the queries keep a Virtuoso-specific variant (`bench/queries/builtin.py`). Both work
around answer-changing bugs in its property-path evaluation, not around a difference of
meaning: each variant was checked row-by-row against the portable text — on the Urban
taxonomy and on a Heritage one with every class instantiated — not merely by row count.

- **`chained_segmentation`** — Virtuoso answers the portable text with **0 rows**, HTTP 200
  and no warning, on every real dataset. The trigger is narrow: it returns the right answer
  for `?obj ?x` (104 rows on ytu3d) and collapses only once the million-row `?s` is
  projected alongside the transitive path. Nothing else moved it — reordering the pattern,
  `+` instead of `*`, the UNION-with-zero-length form OpenLink documents for `*`,
  `OPTION (TRANSITIVE)`, `FILTER EXISTS`, double negation, `MINUS`, an inverse path, every
  `sql:select-option` plan hint, parallelism off, `FROM` vs `GRAPH`: all still 0. Wrapping
  the guard in a subselect **with `ORDER BY`** fixes it (without the `ORDER BY` it is still
  0), so the ordering is what forces Virtuoso to materialise the transitive derived table
  before the big join. It then returns 1 065 720 rows on ytu3d, matching the other nine
  engines.
- **`taxonomical_hierarchy`** — a second, different path bug: chaining an arbitrary-length
  path onto the output of the first (`?c rdfs:subClassOf* ?sup`, then
  `?sup rdfs:subClassOf+ root`) cuts the answer to 15 of 66 rows on ytu3d, and wrapping the
  `?sup2` path in `OPTIONAL` cuts it to 1. The `ORDER BY` trick above does not help. The
  variant drops both guards, which are no-ops on this data: `?c` is already under the root
  (the subselect says so) and the root has no superclass in any of the three ontologies, so
  `?sup rdfs:subClassOf+ root` only ever excludes the root itself — which
  `FILTER (?sup != root)` does directly — and the same argument makes `?sup2`'s guard
  redundant. The `OPTIONAL` never matched nothing either, since `rdfs:subClassOf*` always
  has its zero-length solution. Virtuoso then returns 66 rows on ytu3d.

Neither query carries a `?s a base:Point` guard: `Constitutes` is declared
Point -> Macro_Entity, so `?s` is a point by construction. The guard would also be
unportable — the legacy ontologies call the class `Points`, and the Urban graph never
materialised it on its points at all (0 such triples).

---

[← back to the README](../README.md)
