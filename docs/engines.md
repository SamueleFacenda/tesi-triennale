# Engines

The engines the harness can drive, how each is packaged, and how to add one.

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

## Adding an engine

Subclass `AbstractEngine` (or `DockerEngine`) in `bench/engines/`, implement
`load` / `_start` / `endpoint_url`, and decorate with `@register_engine("name")`. Import
it in `bench/engines/__init__.py`. Querying is inherited.

---

[← back to the README](../README.md)
