"""RDFox — closed source, via docker. Requires a license file.

Set ``RDFOX_LICENSE`` to the path of an ``RDFox.lic`` file; the engine is reported
unavailable otherwise. RDFox serves the SPARQL protocol from a named datastore.
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

_DATASTORE = "bench"
_ROLE = "admin"
_PASSWORD = "admin"


@register_engine("rdfox")
class RDFoxEngine(DockerEngine):
    image = "oxfordsemantic/rdfox:latest"
    container_port = 12110
    license_env = "RDFOX_LICENSE"

    @property
    def _home(self) -> Path:
        return self.storage_dir / "rdfox-home"

    def _base_env(self) -> list[str]:
        lic = os.environ[self.license_env]
        return [
            "-v", f"{self._home}:/home/rdfox/.RDFox",
            "-v", f"{lic}:/opt/RDFox/RDFox.lic:ro",
            "-e", f"RDFOX_ROLE={_ROLE}",
            "-e", f"RDFOX_PASSWORD={_PASSWORD}",
        ]

    def load(self, dataset: Dataset) -> LoadResult:
        self._wipe(self._home)
        self._home.mkdir(parents=True)
        data_dir = os.path.dirname(dataset.path)
        fname = os.path.basename(dataset.path)
        start = time.perf_counter()
        # One-shot container: create a persistent datastore, import the file, save, exit.
        cmd = [
            "-role", _ROLE, "-password", _PASSWORD,
            "exec",
            "dstore", "create", _DATASTORE,
            "active", _DATASTORE,
            "import", f"/data/{fname}",
            "quit",
        ]
        self._rm_container()
        peak = 0
        try:
            self._docker(
                "run", "--name", self.container,
                *self._base_env(), "-v", f"{data_dir}:/data:ro",
                self.image, *cmd, timeout=None,
            )
        finally:
            self._rm_container()
        self._mark_loaded(dataset)
        return LoadResult(time.perf_counter() - start, peak)

    def _start(self) -> None:
        self._run_container(
            self._base_env(),
            cmd=["daemon", "-role", _ROLE, "-password", _PASSWORD],
        )
        self._wait_container_log("REST endpoint", deadline_s=120)

    def is_loaded(self, dataset: Dataset) -> bool:
        return super().is_loaded(dataset) and self._home.exists()

    def endpoint_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/datastores/{_DATASTORE}/sparql"
