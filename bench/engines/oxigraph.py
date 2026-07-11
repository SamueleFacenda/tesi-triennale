"""Oxigraph — native Rust store with a built-in SPARQL HTTP server."""

from __future__ import annotations

import shutil

from ..datasets import Dataset
from ..metrics import run_sampled
from .base import AbstractEngine, EngineError, LoadResult
from .registry import register_engine


@register_engine("oxigraph")
class OxigraphEngine(AbstractEngine):
    required_binaries = ["oxigraph"]

    @property
    def _db(self):
        return self.storage_dir / "data"

    def load(self, dataset: Dataset) -> LoadResult:
        if self._db.exists():
            shutil.rmtree(self._db)
        self._db.mkdir(parents=True)
        res = run_sampled([
            "oxigraph", "load",
            "--location", str(self._db),
            "--file", dataset.path,
            "--format", dataset.content_type,
            "--base", dataset.namespace,
        ])
        if res.returncode != 0:
            raise EngineError(f"oxigraph load failed:\n{res.stderr[-2000:]}")
        self._mark_loaded(dataset)
        return LoadResult(res.elapsed_s, res.peak_rss_bytes)

    def _start(self) -> None:
        self._spawn([
            "oxigraph", "serve",
            "--location", str(self._db),
            "--bind", f"127.0.0.1:{self.port}",
        ])

    def endpoint_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/query"
