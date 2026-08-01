"""rdflib engine: bulk-load, serve SPARQL over HTTP.

Two persistence modes, benchmarked as separate engines:

``--store memory`` (default)
    The graph is held in memory. For persistence the parsed graph is dumped to
    N-Triples on load; serve re-parses that dump (cheaper than re-parsing the original
    RDF/XML/Turtle) so resume needn't touch the source. Startup therefore costs a full
    re-parse (~21k triples/s) and resident memory scales with the graph, which caps this
    mode at roughly 10M triples on a 46 GB machine.

``--store bdb``
    rdflib's BerkeleyDB store, streamed straight to disk: load never holds the graph in
    memory and serve opens the store without re-parsing. Slower per query, but bounded
    memory. Needs the cursor patch in nix/py/rdflib-berkeleydb-cursor.patch — the store
    is unusable as rdflib ships it.

    python -m bench.servers.rdflib_server load  --graph G --file F --format FMT --base NS
    python -m bench.servers.rdflib_server serve --graph G --port P
"""

from __future__ import annotations

import argparse

import rdflib

from ._http import serve

_PARSE_FORMAT = {"ttl": "turtle", "nt": "nt", "rdfxml": "xml"}
_RESULT = {
    "text/csv": ("csv", "text/csv"),
    "application/sparql-results+json": ("json", "application/sparql-results+json"),
    "application/sparql-results+xml": ("xml", "application/sparql-results+xml"),
}
_IDENTIFIER = "bench"


def _open_bdb(path: str, create: bool) -> rdflib.Graph:
    g = rdflib.Graph(store="BerkeleyDB", identifier=_IDENTIFIER)
    if g.open(path, create=create) != rdflib.store.VALID_STORE:
        raise SystemExit(f"rdflib: could not open BerkeleyDB store at {path}")
    return g


def cmd_load(args) -> None:
    import os
    if args.store == "bdb":
        # Parse straight into the store: rdflib's parsers call Graph.add() per triple, so
        # nothing but the BerkeleyDB cache stays resident.
        os.makedirs(args.graph, exist_ok=True)
        g = _open_bdb(args.graph, create=True)
        g.parse(args.file, format=_PARSE_FORMAT[args.format], publicID=args.base)
        g.commit()
        n = len(g)
        g.close()
    else:
        os.makedirs(os.path.dirname(args.graph), exist_ok=True)
        g = rdflib.Graph()
        g.parse(args.file, format=_PARSE_FORMAT[args.format], publicID=args.base)
        g.serialize(destination=args.graph, format="nt", encoding="utf-8")
        n = len(g)
    print(f"rdflib loaded {n} triples")


def cmd_serve(args) -> None:
    if args.store == "bdb":
        g = _open_bdb(args.graph, create=False)
    else:
        g = rdflib.Graph()
        g.parse(args.graph, format="nt")

    def run_query(sparql: str, accept: str):
        fmt, ctype = _RESULT.get(accept.split(",")[0].strip(), _RESULT["text/csv"])
        return g.query(sparql).serialize(format=fmt), ctype

    serve(args.port, run_query)


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    lo = sub.add_parser("load"); lo.set_defaults(func=cmd_load)
    se = sub.add_parser("serve"); se.set_defaults(func=cmd_serve)
    for s in (lo, se):
        s.add_argument("--graph", required=True)
        s.add_argument("--store", choices=("memory", "bdb"), default="memory")
    lo.add_argument("--file", required=True)
    lo.add_argument("--format", default="ttl")
    lo.add_argument("--base", default=None)
    se.add_argument("--port", type=int, required=True)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
