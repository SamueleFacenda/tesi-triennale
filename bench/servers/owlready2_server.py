"""owlready2 engine: load into a persistent SQLite "world", serve SPARQL over HTTP.

Usage:
    python -m bench.servers.owlready2_server load  --world F.sqlite3 --file F --base NS
    python -m bench.servers.owlready2_server serve --world F.sqlite3 --port P

owlready2 has no result serializer, so we emit TSV (header + tab-joined rows) — this is the
added serialization step. Its SPARQL is a subset, so some queries may 400.
"""

from __future__ import annotations

import argparse
import os
import re

from ._http import serve


def _world(path: str):
    from owlready2 import World
    w = World()
    w.set_backend(filename=path, exclusive=False)
    return w


_OWL_FORMAT = {"rdfxml": "rdfxml", "nt": "ntriples"}


def cmd_load(args) -> None:
    w = _world(args.world)
    # force the parser: owlready2's auto-detect mis-reads some RDF/XML as OWL/XML
    w.get_ontology("file://" + os.path.abspath(args.file)).load(format=_OWL_FORMAT[args.format])
    w.save()
    print("owlready2 world saved")


def _var_names(sparql: str, width: int) -> list[str]:
    m = re.search(r"select\s+(?:distinct\s+)?(.*?)\s+where", sparql, re.I | re.S)
    names: list[str] = []
    if m:
        clause = re.sub(r"\([^()]*?as\s+\?(\w+)\s*\)", r"?\1", m.group(1), flags=re.I)
        names = re.findall(r"\?(\w+)", clause)
    if len(names) != width:
        names = [f"c{i}" for i in range(width)]
    return names


def _cell(v) -> str:
    if v is None:
        return ""
    s = getattr(v, "iri", None) or str(v)
    return s.replace("\t", " ").replace("\n", " ").replace("\r", " ")


def cmd_serve(args) -> None:
    w = _world(args.world)

    def run_query(sparql: str, accept: str):
        # treat IRIs absent from the store as no-match (empty), like every other engine,
        # instead of raising — otherwise a query naming an unused class 400s
        rows = [list(r) for r in w.sparql(sparql, error_on_undefined_entities=False)]
        width = len(rows[0]) if rows else len(_var_names(sparql, 0)) or 1
        header = "\t".join(_var_names(sparql, width))
        lines = [header] + ["\t".join(_cell(v) for v in r) for r in rows]
        return ("\n".join(lines) + "\n").encode("utf-8"), "text/tab-separated-values"

    serve(args.port, run_query)


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    lo = sub.add_parser("load"); lo.set_defaults(func=cmd_load)
    se = sub.add_parser("serve"); se.set_defaults(func=cmd_serve)
    for s in (lo, se):
        s.add_argument("--world", required=True)
    lo.add_argument("--file", required=True)
    lo.add_argument("--format", default="rdfxml")
    lo.add_argument("--base", default=None)
    se.add_argument("--port", type=int, required=True)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
