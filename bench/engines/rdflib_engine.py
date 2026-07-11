"""rdflib — embedded engine wrapped in a tiny HTTP SPARQL server (BerkeleyDB persistence)."""

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
            sys.executable, "-m", _MODULE, "load",
            "--graph", str(self._store),
            "--file", inp.path, "--format", inp.fmt, "--base", inp.namespace,
        ], log_path=self.log_path)
        if not res.ok:
            raise EngineError(f"rdflib load failed ({res.describe_failure()}); "
                              f"see {self.log_path}\n{res.output[-1500:]}")
        self._mark_loaded(dataset)
        return LoadResult(res.elapsed_s, res.peak_rss_bytes)

    def is_loaded(self, dataset: Dataset) -> bool:
        return super().is_loaded(dataset) and self._store.exists()

    def _start(self) -> None:
        self._spawn([
            sys.executable, "-m", _MODULE, "serve",
            "--graph", str(self._store), "--port", str(self.port),
        ])

    def endpoint_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/sparql"
