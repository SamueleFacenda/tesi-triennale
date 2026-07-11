"""Stardog — via docker. Requires a license file.

Set ``STARDOG_LICENSE`` to the path of a ``stardog-license-key.bin``; the engine is
reported unavailable otherwise. A database is created and loaded with ``stardog-admin``,
then queried over the HTTP endpoint ``/{db}/query``.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from ..datasets import Dataset
from ..metrics import RssSampler
from .base import EngineError, LoadResult
from .docker_base import DockerEngine
from .registry import register_engine

_DB = "bench"
_USER = "admin"
_PASSWORD = "admin"


@register_engine("stardog")
class StardogEngine(DockerEngine):
    image = "stardog/stardog:latest"
    container_port = 5820
    license_env = "STARDOG_LICENSE"

    @property
    def _home(self) -> Path:
        return self.storage_dir / "stardog-home"

    def _base_env(self) -> list[str]:
        lic = os.environ[self.license_env]
        # Stardog wants heap and off-heap ("direct") memory split ~evenly.
        half = max(1, self.tuning.budget_gb // 2)
        java_args = f"-Xms{half}g -Xmx{half}g -XX:MaxDirectMemorySize={half}g"
        return [
            "-e", f"STARDOG_SERVER_JAVA_ARGS={java_args}",
            "-v", f"{self._home}:/var/opt/stardog",
            "-v", f"{lic}:/var/opt/stardog/stardog-license-key.bin:ro",
        ]

    def _admin(self, *args: str, timeout: float | None = 120):
        return self._docker("exec", self.container, "stardog-admin", *args, timeout=timeout)

    def load(self, dataset: Dataset) -> LoadResult:
        inp = self.resolve_input(dataset)
        self._wipe(self._home)
        self._home.mkdir(parents=True)
        data_dir = os.path.dirname(inp.path)
        fname = os.path.basename(inp.path)
        start = time.perf_counter()
        self._run_container(self._base_env() + ["-v", f"{data_dir}:/data:ro"])
        sampler = RssSampler(self._sample_rss).start()
        try:
            self._wait_container_log("Stardog server", deadline_s=180)
            r = self._admin("db", "create", "-n", _DB, f"/data/{fname}", timeout=None)
            if r.returncode != 0:
                raise EngineError(f"stardog db create/load failed:\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
        finally:
            peak = sampler.stop()
            self._dump_container_log()
        # keep the container running for querying; do not remove here
        self._mark_loaded(dataset)
        return LoadResult(time.perf_counter() - start, peak)

    def _start(self) -> None:
        # If load left a container running, reuse it; else start fresh.
        insp = self._docker("inspect", "-f", "{{.State.Running}}", self.container, check=False)
        if insp.returncode != 0 or insp.stdout.strip() != "true":
            self._run_container(self._base_env())
            self._wait_container_log("Stardog server", deadline_s=180)

    def is_loaded(self, dataset: Dataset) -> bool:
        return super().is_loaded(dataset) and self._home.exists()

    def endpoint_url(self) -> str:
        return f"http://{_USER}:{_PASSWORD}@127.0.0.1:{self.port}/{_DB}/query"
