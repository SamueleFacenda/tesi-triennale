"""Virtuoso Open Source — via the official openlink docker image.

Not packaged in this nixpkgs, so docker is the fallback (as allowed by the spec).
Virtuoso has no implicit default-graph union, so data is loaded into ``BENCH_GRAPH``
and queries are sent with a matching ``default-graph-uri``. Its endpoint also truncates a
single response at 2^20 rows, so queries are paged (``chunk_rows``).

Its tuning is an ini file, not env vars: the image turns ``VIRT_Parameters_*`` into
``/database/virtuoso.ini`` only when it *generates* that file, which happens once, on the
first boot into an empty db dir. A resumed run therefore serves with the ini written by
its first load and silently ignores every setting added since — hence
:meth:`VirtuosoEngine._patch_ini`, which rewrites the keys in place before each start.
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

# Virtuoso's endpoint silently truncates one response at 2^20 = 1048576 rows (HTTP 200,
# no warning), so every result above that was simply cut off. Fetch it in pages instead,
# with the round number just below the cap that the 3dont viewer already uses.
_CHUNK_ROWS = 1_000_000

# Virtuoso refuses ORDER BY combined with LIMIT/OFFSET when offset+limit exceeds
# MaxSortedTopRows ("Sorted TOP clause specifies more then N rows to sort", default
# 10000), which the paging above trips on any ordered query — the LIMIT value alone is
# checked, so `taxonomical_hierarchy` failed on it with a two-dozen-row answer. Raise the
# ceiling well past the largest result the benchmark asks for: a guard, not an allocation.
_MAX_SORTED_TOP_ROWS = 100_000_000


@register_engine("virtuoso")
class VirtuosoEngine(DockerEngine):
    image = "openlink/virtuoso-opensource-7:latest"
    container_port = 8890
    default_graph = BENCH_GRAPH
    chunk_rows = _CHUNK_ROWS
    # inherits the tsv default: measured (15k-row result) json 203ms vs tsv/csv ~74ms,
    # so JSON is ~2.7x slower here — exactly what to avoid.

    @property
    def _db_dir(self) -> Path:
        return self.storage_dir / "database"

    def _ini_params(self) -> dict[str, str]:
        """The ``[Parameters]`` settings this engine needs, whatever the db dir's age.

        NumberOfBuffers / MaxDirtyBuffers are Virtuoso's key perf knobs; the defaults
        (~10k buffers) are tuned for ~1 GB and cripple large datasets. One dict feeds both
        ways of applying them (fresh dir: env; existing dir: `_patch_ini`), so the two
        cannot drift apart.
        """
        return {
            "DirsAllowed": "., /data, /database",
            "NumberOfBuffers": str(self.tuning.virtuoso_buffers),
            "MaxDirtyBuffers": str(self.tuning.virtuoso_dirty_buffers),
            "MaxQueryMem": f"{max(2, self.tuning.budget_gb // 4)}G",
            "ThreadsPerQuery": str(self.tuning.cores),
            "MaxSortedTopRows": str(_MAX_SORTED_TOP_ROWS),
        }

    def _base_env(self) -> list[str]:
        env = ["-e", f"DBA_PASSWORD={_PASSWORD}"]
        for key, value in self._ini_params().items():
            env += ["-e", f"VIRT_Parameters_{key}={value}"]
        return env + ["-v", f"{self._db_dir}:/database"]

    def _patch_ini(self) -> None:
        """Force `_ini_params` into an existing ``virtuoso.ini``; no-op on a fresh dir.

        The container writes the file as its own uid, so the host user can neither read nor
        edit it — do it from a throwaway root container, the same way `_wipe` deletes
        root-owned data. Each key is dropped wherever it sits and re-emitted right below
        the ``[Parameters]`` header, so this is idempotent and runs before every start.
        """
        if not (self._db_dir / "virtuoso.ini").is_file():
            return  # the image's entrypoint will generate it from _base_env()
        params = self._ini_params()
        adds = "".join(f'print "{k} = {v}"; ' for k, v in params.items())
        prog = (
            rf'/^[ \t]*\[Parameters\]/ {{ print; {adds}next }} '
            rf'/^[ \t]*({"|".join(params)})[ \t]*=/ {{ next }} '
            '{ print }'
        )
        # write back through `cat`, not `mv`: that keeps the file's inode, owner and mode
        # (the server runs as the container's own uid, not root) and leaves no stray file
        # in the db dir, whose size is a reported metric.
        self._docker(
            "run", "--rm", "-v", f"{self._db_dir}:/database", "busybox", "sh", "-c",
            f"awk '{prog}' /database/virtuoso.ini > /tmp/virtuoso.ini "
            "&& cat /tmp/virtuoso.ini > /database/virtuoso.ini",
            timeout=120,
        )

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
        self._patch_ini()
        self._run_container(self._base_env())
        self._wait_container_log("Server online", deadline_s=180)

    def is_loaded(self, dataset: Dataset) -> bool:
        return super().is_loaded(dataset) and self._db_dir.exists()

    def endpoint_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/sparql"
