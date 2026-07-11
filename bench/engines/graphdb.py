"""Ontotext GraphDB (Free edition) — via docker.

The Free tier needs no license key. A repository is created through GraphDB's REST API,
then the dataset is bulk-imported via the RDF4J statements endpoint into the default
graph. SPARQL endpoint: ``/repositories/{repo}``.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from ..datasets import Dataset
from ..metrics import RssSampler
from .base import EngineError, LoadResult
from .docker_base import DockerEngine
from .registry import register_engine

_REPO = "bench"

_REPO_CONFIG = """
@prefix rep: <http://www.openrdf.org/config/repository#> .
@prefix sr: <http://www.openrdf.org/config/repository/sail#> .
@prefix sail: <http://www.openrdf.org/config/sail#> .
@prefix graphdb: <http://www.ontotext.com/config/graphdb#> .

[] a rep:Repository ;
   rep:repositoryID "%s" ;
   rep:repositoryImpl [
       rep:repositoryType "graphdb:SailRepository" ;
       sr:sailImpl [ sail:sailType "graphdb:Sail" ; graphdb:ruleset "empty" ]
   ] .
""" % _REPO


@register_engine("graphdb")
class GraphDBEngine(DockerEngine):
    image = "ontotext/graphdb:10.7.0"
    container_port = 7200
    # measured (15k-row result): csv 46ms vs tsv 84ms vs json 171ms — GraphDB's TSV
    # serializer is ~1.8x slower than CSV, so override the tsv default to csv here.
    result_format = "csv"

    @property
    def _data_dir(self) -> Path:
        return self.storage_dir / "graphdb-home"

    def _base_env(self) -> list[str]:
        # GDB_HEAP_SIZE sets -Xms and -Xmx; the default heap is far too small for big data.
        return [
            "-e", f"GDB_HEAP_SIZE={self.tuning.budget_gb}g",
            "-v", f"{self._data_dir}:/opt/graphdb/home",
        ]

    def _base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _create_repo(self) -> None:
        import requests
        cfg = self.storage_dir / "repo-config.ttl"
        cfg.write_text(_REPO_CONFIG)
        with open(cfg, "rb") as f:
            r = requests.post(
                f"{self._base_url()}/rest/repositories",
                files={"config": ("repo-config.ttl", f, "application/x-turtle")},
                timeout=60,
            )
        if r.status_code not in (200, 201):
            raise EngineError(f"graphdb repo create failed: HTTP {r.status_code}: {r.text[:500]}")

    def _import(self, inp) -> None:
        import requests
        with open(inp.path, "rb") as f:
            r = requests.post(
                f"{self._base_url()}/repositories/{_REPO}/statements",
                data=f, headers={"Content-Type": inp.content_type}, timeout=None,
            )
        if r.status_code not in (200, 204):
            raise EngineError(f"graphdb import failed: HTTP {r.status_code}: {r.text[:500]}")

    def load(self, dataset: Dataset) -> LoadResult:
        inp = self.resolve_input(dataset)
        self._wipe(self._data_dir)
        self._data_dir.mkdir(parents=True)
        start = time.perf_counter()
        self._run_container(self._base_env())
        sampler = RssSampler(self._sample_rss).start()
        try:
            self._wait_http_ready()
            self._create_repo()
            self._import(inp)
        finally:
            peak = sampler.stop()
            self._dump_container_log()
            self._rm_container()
        self._mark_loaded(dataset)
        return LoadResult(time.perf_counter() - start, peak)

    def _wait_http_ready(self, deadline_s: float = 180.0) -> None:
        import requests
        end = time.perf_counter() + deadline_s
        while time.perf_counter() < end:
            try:
                if requests.get(f"{self._base_url()}/rest/repositories", timeout=5).status_code == 200:
                    return
            except requests.RequestException:
                pass
            insp = self._docker("inspect", "-f", "{{.State.Running}}", self.container, check=False)
            if insp.stdout.strip() == "false":
                raise EngineError(f"graphdb: container exited early; see `docker logs {self.container}`")
            time.sleep(1.0)
        raise EngineError(f"graphdb: not ready within {deadline_s}s")

    def _start(self) -> None:
        self._run_container(self._base_env())
        self._wait_http_ready()

    def is_loaded(self, dataset: Dataset) -> bool:
        return super().is_loaded(dataset) and self._data_dir.exists()

    def endpoint_url(self) -> str:
        return f"{self._base_url()}/repositories/{_REPO}"
