"""rdflib engine: bulk-load, serve SPARQL over HTTP.

rdflib's BerkeleyDB store is broken in this version, so the graph is held in memory. For
persistence the parsed graph is dumped to N-Triples on load; serve re-parses that dump
(cheaper than re-parsing the original RDF/XML/Turtle) so resume needn't touch the source.

    python -m bench.servers.rdflib_server load  --graph G.nt --file F --format FMT --base NS
    python -m bench.servers.rdflib_server serve --graph G.nt --port P
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


def cmd_load(args) -> None:
    import os
    os.makedirs(os.path.dirname(args.graph), exist_ok=True)
    g = rdflib.Graph()
    g.parse(args.file, format=_PARSE_FORMAT[args.format], publicID=args.base)
    g.serialize(destination=args.graph, format="nt", encoding="utf-8")
    print(f"rdflib loaded {len(g)} triples")


def cmd_serve(args) -> None:
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
    lo.add_argument("--file", required=True)
    lo.add_argument("--format", default="ttl")
    lo.add_argument("--base", default=None)
    se.add_argument("--port", type=int, required=True)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
