"""Virtuoso Open Source — via the official openlink docker image.

Not packaged in this nixpkgs, so docker is the fallback (as allowed by the spec).
Virtuoso has no implicit default-graph union, so data is loaded into ``BENCH_GRAPH``
and queries are sent with a matching ``default-graph-uri``.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..datasets import Dataset
from ..metrics import RssSampler
from .base import BENCH_GRAPH, EngineError, LoadResult
from .docker_base import DockerEngine
from .registry import register_engine

_PASSWORD = "benchdba"


@register_engine("virtuoso")
class VirtuosoEngine(DockerEngine):
    image = "openlink/virtuoso-opensource-7:latest"
    container_port = 8890
    default_graph = BENCH_GRAPH

    @property
    def _db_dir(self) -> Path:
        return self.storage_dir / "database"

    def _base_env(self) -> list[str]:
        # NumberOfBuffers / MaxDirtyBuffers are Virtuoso's key perf knobs; the defaults
        # (~10k buffers) are tuned for ~1 GB and cripple large datasets.
        return [
            "-e", f"DBA_PASSWORD={_PASSWORD}",
            "-e", "VIRT_Parameters_DirsAllowed=., /data, /database",
            "-e", f"VIRT_Parameters_NumberOfBuffers={self.tuning.virtuoso_buffers}",
            "-e", f"VIRT_Parameters_MaxDirtyBuffers={self.tuning.virtuoso_dirty_buffers}",
            "-e", f"VIRT_Parameters_MaxQueryMem={max(2, self.tuning.budget_gb // 4)}G",
            "-e", f"VIRT_Parameters_ThreadsPerQuery={self.tuning.cores}",
            "-v", f"{self._db_dir}:/database",
        ]

    def load(self, dataset: Dataset) -> LoadResult:
        import time
        inp = self.resolve_input(dataset)
        self._wipe(self._db_dir)
        self._db_dir.mkdir(parents=True)
        data_dir = os.path.dirname(inp.path)
        fname = os.path.basename(inp.path)

        start = time.perf_counter()
        # Start a loader container with the dataset dir mounted read-only.
        self._run_container(self._base_env() + ["-v", f"{data_dir}:/data:ro"])
        sampler = RssSampler(self._sample_rss).start()
        try:
            self._wait_container_log("Server online", deadline_s=180)
            isql = (
                f"ld_dir('/data', '{fname}', '{BENCH_GRAPH}');\n"
                "rdf_loader_run();\n"
                "checkpoint;\n"
            )
            proc = self._exec_isql(isql)
            if proc.returncode != 0:
                raise EngineError(f"virtuoso load failed:\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
        finally:
            peak = sampler.stop()
            self._dump_container_log()
            self._rm_container()
        self._mark_loaded(dataset)
        return LoadResult(time.perf_counter() - start, peak)

    def _exec_isql(self, script: str):
        import subprocess
        return subprocess.run(
            [self._rt(), "exec", "-i", self.container, "isql", "1111", "dba", _PASSWORD],
            input=script, capture_output=True, text=True, timeout=None,
        )

    def _start(self) -> None:
        self._run_container(self._base_env())
        self._wait_container_log("Server online", deadline_s=180)

    def is_loaded(self, dataset: Dataset) -> bool:
        return super().is_loaded(dataset) and self._db_dir.exists()

    def endpoint_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/sparql"
