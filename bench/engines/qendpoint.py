"""qEndpoint — HDT-backed store with a Spring-Boot SPARQL HTTP endpoint."""

from __future__ import annotations

import shutil

from ..datasets import Dataset
from ..metrics import run_sampled
from .base import AbstractEngine, EngineError, LoadResult
from .registry import register_engine


# Spring Boot flags qEndpoint needs (mirrors 3DOnt's dev shell); kept alongside -Xmx.
_SPRING = ("-Dspring.autoconfigure.exclude="
           "org.springframework.boot.autoconfigure.http.client.HttpClientAutoConfiguration "
           "-Dspring.devtools.restart.enabled=false")


@register_engine("qendpoint")
class QendpointEngine(AbstractEngine):
    required_binaries = ["qendpoint.sh", "qendpoint-rdf2hdt.sh"]
    fixed_port = 1234  # qEndpoint's Spring server hardcodes this port

    def _hdt_options(self) -> str:
        # HDT build tuning (from 3DOnt): all six permutations for fast queries, on-disk
        # cat-tree loader for datasets larger than RAM, parallel compression.
        workers = self.tuning.load_workers
        return ";".join([
            "loader.type=disk",
            f"loader.disk.compressWorker={workers}",
            "bitmaptriples.index.others=spo,ops,pos,osp,sop,pso",
            "dictionary.type=dictionaryMultiObj",
            "tempDictionary.impl=hashPsfc",
            f"loader.cattree.kcat={workers}",
            "profiler=false",
        ])

    @property
    def _location(self):
        # qEndpoint's `locationEndpoint`: holds native-store/ and hdt-store/.
        return self.storage_dir / "qendpoint"

    @property
    def _hdt(self):
        return self._location / "hdt-store" / "index_dev.hdt"

    def load(self, dataset: Dataset) -> LoadResult:
        inp = self.resolve_input(dataset)
        if self._location.exists():
            shutil.rmtree(self._location)
        self._hdt.parent.mkdir(parents=True)
        # Build an indexed HDT from the RDF file (base URI = ontology namespace).
        res = run_sampled([
            "qendpoint-rdf2hdt.sh",
            "-index",
            "-base", inp.namespace,
            "-options", self._hdt_options(),
            inp.path,
            str(self._hdt),
        ], env={"JAVA_OPTIONS": f"{self.tuning.xmx} {_SPRING}"}, log_path=self.log_path)
        if not res.ok:
            raise EngineError(f"qendpoint-rdf2hdt failed ({res.describe_failure()}); "
                              f"see {self.log_path}\n{res.output[-1500:]}")
        self._mark_loaded(dataset)
        return LoadResult(res.elapsed_s, res.peak_rss_bytes)

    def _start(self) -> None:
        # qEndpoint's launcher does not forward program args to Spring and hardcodes port
        # 1234. Running in storage_dir makes the default `locationEndpoint=./qendpoint`
        # resolve to where load() built the HDT.
        self._spawn(["qendpoint.sh"], cwd=self.storage_dir,
                    env={"JAVA_OPTIONS": f"{self.tuning.xmx} {_SPRING}"})

    def endpoint_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/api/endpoint/sparql"
