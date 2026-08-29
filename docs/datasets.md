# Queries and datasets

How queries are templated, what counts as a dataset, and how to prepare the source graphs.

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

`grande` ships only as Turtle, which owlready2 cannot read (it has no Turtle parser —
only N-Triples, RDF/XML and OWL/XML). Its `.nt` is converted once, alongside the source:

```bash
oxigraph convert -f ~/downloads/ontos/grande_3DGraph.ttl \
                 -t ~/downloads/ontos/grande_3DGraph.nt   # 4.9 GB -> 19.4 GB, ~10 min
```

Only owlready2 picks it up; every other engine still loads the Turtle. `santa_chiara` is
Turtle-only too and would need the same treatment before owlready2 can run it.

---

[← back to the README](../README.md)
