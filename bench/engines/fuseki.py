"""Apache Jena Fuseki — TDB2 store + Fuseki HTTP server."""

from __future__ import annotations

import shutil

from ..datasets import Dataset
from ..metrics import run_sampled
from .base import AbstractEngine, EngineError, LoadResult
from .registry import register_engine

_DATASET = "ds"


@register_engine("fuseki")
class FusekiEngine(AbstractEngine):
    required_binaries = ["fuseki-server", "tdb2.tdbloader"]

    @property
    def _tdb(self):
        return self.storage_dir / "tdb2"

    def load(self, dataset: Dataset) -> LoadResult:
        inp = self.resolve_input(dataset)
        if self._tdb.exists():
            shutil.rmtree(self._tdb)
        self._tdb.mkdir(parents=True)
        res = run_sampled([
            "tdb2.tdbloader",
            "--loc", str(self._tdb),
            "--loader", "parallel",
            inp.path,
        ], env={"JVM_ARGS": self.tuning.xmx}, log_path=self.log_path)
        if not res.ok:
            raise EngineError(f"tdb2.tdbloader failed ({res.describe_failure()}); "
                              f"see {self.log_path}\n{res.output[-1500:]}")
        self._mark_loaded(dataset)
        return LoadResult(res.elapsed_s, res.peak_rss_bytes)

    def _start(self) -> None:
        self._spawn([
            "fuseki-server",
            "--loc", str(self._tdb),
            "--port", str(self.port),
            "--localhost",
            f"/{_DATASET}",
        ], env={"JVM_ARGS": self.tuning.xmx})

    def endpoint_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/{_DATASET}/sparql"
