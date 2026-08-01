"""rdflib — embedded engine wrapped in a tiny HTTP SPARQL server.

Two engines share this module, differing only in how the graph is persisted:

``rdflib``
    In-memory graph, dumped to / re-parsed from N-Triples. What people actually use, but
    resident memory scales with the graph and startup re-parses the whole dump.

``rdflib-bdb``
    rdflib's BerkeleyDB store, streamed to disk. Bounded memory and instant startup, at
    the cost of slower queries. Requires the cursor patch (see ``available``).
"""

from __future__ import annotations

import importlib.util
import sys

from ..datasets import Dataset
from ..metrics import run_sampled
from .base import AbstractEngine, EngineError, LoadResult
from .registry import register_engine

_MODULE = "bench.servers.rdflib_server"


@register_engine("rdflib")
class RdflibEngine(AbstractEngine):
    result_format = "csv"  # rdflib serializes csv/json/xml (no tsv)
    # serve() re-parses the whole N-Triples dump before it binds the port, at a measured
    # ~21k triples/s: ~9 min for ytu3d's 9.6M triples, half an hour for the big graphs.
    health_deadline_s = 3600.0
    # persistence mode passed to the server module
    _STORE = "memory"

    @classmethod
    def available(cls) -> tuple[bool, str]:
        if importlib.util.find_spec("rdflib") is None:
            return False, "rdflib not importable"
        return True, "ok"

    @property
    def _store(self):
        return self.storage_dir / "graph.nt"

    def load(self, dataset: Dataset) -> LoadResult:
        inp = self.resolve_input(dataset)
        res = run_sampled([
            sys.executable, "-m", _MODULE, "load", "--store", self._STORE,
            "--graph", str(self._store),
            "--file", inp.path, "--format", inp.fmt, "--base", inp.namespace,
        ], log_path=self.log_path)
        if not res.ok:
            raise EngineError(f"{self.name} load failed ({res.describe_failure()}); "
                              f"see {self.log_path}\n{res.output[-1500:]}")
        self._mark_loaded(dataset)
        return LoadResult(res.elapsed_s, res.peak_rss_bytes)

    def is_loaded(self, dataset: Dataset) -> bool:
        return super().is_loaded(dataset) and self._store.exists()

    def _start(self) -> None:
        self._spawn([
            sys.executable, "-m", _MODULE, "serve", "--store", self._STORE,
            "--graph", str(self._store), "--port", str(self.port),
        ])

    def endpoint_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/sparql"


@register_engine("rdflib-bdb")
class RdflibBdbEngine(RdflibEngine):
    _STORE = "bdb"
    # opening a BerkeleyDB store is O(1) — no re-parse, so the base default is plenty
    health_deadline_s = 120.0

    @classmethod
    def available(cls) -> tuple[bool, str]:
        ok, reason = super().available()
        if not ok:
            return ok, reason
        if importlib.util.find_spec("berkeleydb") is None:
            return False, "berkeleydb not importable"
        # rdflib ships the BerkeleyDB store with five `cursor.next` calls missing their
        # parentheses, which makes every multi-row query raise TypeError. nix applies
        # nix/py/rdflib-berkeleydb-cursor.patch; refuse to produce numbers without it.
        import inspect
        import re
        from rdflib.plugins.stores import berkeleydb
        if re.search(r"cursor\.next\b(?!\()", inspect.getsource(berkeleydb)):
            return False, ("rdflib's BerkeleyDB store is unpatched (cursor.next bug); "
                           "see nix/py/rdflib-berkeleydb-cursor.patch")
        return True, "ok"

    @property
    def _store(self):
        return self.storage_dir / "bdb"   # a directory, not a dump file
