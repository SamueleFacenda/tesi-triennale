"""RDFox — closed source, via docker. Requires a license file.

Set ``RDFOX_LICENSE`` to the path of an ``RDFox.lic`` file (its contents are passed to the
container as ``RDFOX_LICENSE_CONTENT``). Data is loaded into a persistent, on-disk server
directory (``-persistence file``) via a one-shot ``init``; ``daemon`` then serves it over
the REST SPARQL endpoint. RDFox is strict about literals, so the datastore uses
``invalid-literal-policy = as-string`` to tolerate the datasets' non-canonical decimals
(e.g. ``"1e-08"^^xsd:decimal``).
"""

from __future__ import annotations

import os
import subprocess
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
    input_formats = ["ttl", "nt"]  # RDFox reads Turtle/N-Triples/TriG, not RDF/XML
    result_format = "csv"

    @property
    def _home(self) -> Path:
        return self.storage_dir / "rdfox-home"

    def _license_content(self) -> str:
        return Path(os.environ[self.license_env]).read_text()

    def _env(self) -> list[str]:
        return [
            "-e", f"RDFOX_ROLE={_ROLE}",
            "-e", f"RDFOX_PASSWORD={_PASSWORD}",
            "-e", f"RDFOX_LICENSE_CONTENT={self._license_content()}",
            "-v", f"{self._home}:/home/rdfox/.RDFox",
        ]

    def load(self, dataset: Dataset) -> LoadResult:
        inp = self.resolve_input(dataset)
        self._wipe(self._home)
        self._home.mkdir(parents=True)
        self._home.chmod(0o777)  # container runs as the 'rdfox' user
        data_dir = os.path.dirname(inp.path)
        fname = os.path.basename(inp.path)

        self._rm_container()
        start = time.perf_counter()
        # one-shot: initialise a persistent server dir, create+load the datastore, exit
        cmd = [
            self._rt(), "run", "--name", self.container,
            *self._env(), "-v", f"{data_dir}:/data:ro",
            self.image,
            "-persistence", "file", "-channel", "unsecure", "init", ".",
            f"dstore create {_DATASTORE}",
            f"active {_DATASTORE}",
            "dsprop set invalid-literal-policy as-string",
            f"import /data/{fname}",
        ]
        sampler = RssSampler(self._sample_rss, self.rss_sample_interval).start()
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=None)
        finally:
            peak = sampler.stop()
        with open(self.log_path, "ab") as f:
            f.write((proc.stdout + proc.stderr).encode("utf-8", "replace"))
        self._rm_container()
        if proc.returncode != 0:
            raise EngineError(f"rdfox init/import failed; see {self.log_path}\n{proc.stderr[-1500:]}")
        self._mark_loaded(dataset)
        return LoadResult(time.perf_counter() - start, peak)

    def _start(self) -> None:
        self._run_container(self._env(), cmd=["-persistence", "file", "-channel", "unsecure", "daemon"])

    def is_loaded(self, dataset: Dataset) -> bool:
        return super().is_loaded(dataset) and self._home.exists()

    def endpoint_url(self) -> str:
        # basic auth in the URL (requests honors userinfo)
        return f"http://{_ROLE}:{_PASSWORD}@127.0.0.1:{self.port}/datastores/{_DATASTORE}/sparql"
