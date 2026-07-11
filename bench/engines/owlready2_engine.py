"""owlready2 — embedded engine wrapped in a tiny HTTP SPARQL server (SQLite world persistence)."""

from __future__ import annotations

import importlib.util
import sys

from ..datasets import Dataset
from ..metrics import run_sampled
from .base import AbstractEngine, EngineError, LoadResult
from .registry import register_engine

_MODULE = "bench.servers.owlready2_server"


@register_engine("owlready2")
class Owlready2Engine(AbstractEngine):
    input_formats = ["rdfxml", "nt"]  # owlready2 reads RDF/XML and N-Triples, not Turtle
    result_format = "tsv"

    @classmethod
    def available(cls) -> tuple[bool, str]:
        if importlib.util.find_spec("owlready2") is None:
            return False, "owlready2 not importable"
        return True, "ok"

    @property
    def _world(self):
        return self.storage_dir / "world.sqlite3"

    def load(self, dataset: Dataset) -> LoadResult:
        inp = self.resolve_input(dataset)
        if self._world.exists():
            self._world.unlink()
        res = run_sampled([
            sys.executable, "-m", _MODULE, "load",
            "--world", str(self._world), "--file", inp.path,
            "--format", inp.fmt, "--base", inp.namespace,
        ], log_path=self.log_path)
        if not res.ok:
            raise EngineError(f"owlready2 load failed ({res.describe_failure()}); "
                              f"see {self.log_path}\n{res.output[-1500:]}")
        self._mark_loaded(dataset)
        return LoadResult(res.elapsed_s, res.peak_rss_bytes)

    def is_loaded(self, dataset: Dataset) -> bool:
        return super().is_loaded(dataset) and self._world.exists()

    def _start(self) -> None:
        self._spawn([
            sys.executable, "-m", _MODULE, "serve",
            "--world", str(self._world), "--port", str(self.port),
        ])

    def endpoint_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/sparql"
