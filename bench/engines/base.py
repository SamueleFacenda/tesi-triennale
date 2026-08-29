"""Abstract engine lifecycle.

Every engine is normalised to an HTTP SPARQL endpoint:

    provision -> load(dataset) -> start() -> query()* -> stop()

Loading is engine-specific; querying is the single shared HTTP path (``query``). The
runner drives one engine, one dataset and one query at a time (no parallelism) so each
query gets the machine's full resources.
"""

from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import psutil
import requests

from ..datasets import Dataset, ResolvedInput
from ..metrics import RssSampler, rss_of_tree
from ..tuning import Tuning
from .http_client import QueryError, QueryResult, SparqlHttpClient

_MARKER = ".loaded"

# Named graph used by engines that cannot query an implicit default-graph union
# (Virtuoso). Native stores load into their real default graph and ignore this.
BENCH_GRAPH = "urn:bench:graph"


@dataclass
class LoadResult:
    load_time_s: float
    peak_rss_bytes: int


class EngineError(Exception):
    pass


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def which(binary: str) -> str | None:
    return shutil.which(binary)


class AbstractEngine(ABC):
    name: ClassVar[str] = ""
    # binaries that must be on PATH for a native engine (overridden per engine)
    required_binaries: ClassVar[list[str]] = []
    # default-graph-uri to send on every query (set by engines lacking a default-graph
    # union, e.g. Virtuoso). None => query the store's real default graph.
    default_graph: ClassVar[str | None] = None
    # engines that can only bind a fixed port (e.g. qEndpoint hardcodes 1234). Safe
    # because runs are sequential — only one server is ever up at a time.
    fixed_port: ClassVar[int | None] = None
    # RDF serializations this engine can bulk-load (most take all; QLever/RDFox don't
    # read RDF/XML). Preference among the accepted+available formats is dataset-side.
    input_formats: ClassVar[list[str]] = ["ttl", "nt", "rdfxml"]
    # RSS sampling cadence (s). Docker engines read `docker stats` (~1-2s/call), so they
    # sample far less often than native (psutil, ~instant).
    rss_sample_interval: ClassVar[float] = 0.05
    # SPARQL result serialization this engine emits fastest (key into FORMATS).
    # Measured on a 15k-row result: TSV/CSV beat JSON everywhere by 2-6x (e.g. fuseki
    # json 255ms vs tsv 43ms), and TSV parses reliably (line count, no embedded newlines).
    # So TSV is the default; only GraphDB overrides (its CSV is ~1.8x faster than its TSV).
    result_format: ClassVar[str] = "tsv"
    # Page size (rows) for endpoints that truncate a single response, so a big result
    # must be fetched in OFFSET/LIMIT pages. None = one request per query.
    chunk_rows: ClassVar[int | None] = None
    # How long the endpoint may take to answer after _start(). Engines whose startup cost
    # scales with the graph (rdflib re-parses its whole dump into memory) need far more
    # than the default. Safe to raise: _wait_healthy aborts as soon as a spawned process
    # dies, so an OOM kill still surfaces immediately instead of waiting this out.
    health_deadline_s: ClassVar[float] = 120.0

    def __init__(self, storage_dir: str | Path, timeout_s: float = 300.0,
                 port: int | None = None, tuning: Tuning | None = None,
                 result_format: str | None = None):
        # absolute: docker -v mounts and qEndpoint's CWD-relative config both need it
        self.storage_dir = Path(storage_dir).resolve()
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.timeout_s = timeout_s
        self.tuning = tuning or Tuning.detect()
        # config override wins over the engine's class default
        self.effective_result_format = result_format or self.result_format
        self.port = port or self.fixed_port or free_port()
        self.log_path = self.storage_dir.parent / f"{self.name}.log"
        self._procs: list[subprocess.Popen] = []
        self._server_procs: list[psutil.Process] = []
        self._client: SparqlHttpClient | None = None
        self._sampler: RssSampler | None = None

    # ------------------------------------------------------------------ availability
    @classmethod
    def available(cls) -> tuple[bool, str]:
        """Whether this engine can run here. Override for docker/license engines."""
        missing = [b for b in cls.required_binaries if which(b) is None]
        if missing:
            return False, f"missing binaries: {', '.join(missing)}"
        return True, "ok"

    def provision(self) -> None:
        """Ensure the engine is ready (pull docker image, etc.). No-op for nix natives."""

    # ------------------------------------------------------------------ loading
    @abstractmethod
    def load(self, dataset: Dataset) -> LoadResult:
        """Bulk-load ``dataset`` into ``storage_dir``. Timed + RSS-sampled internally."""

    def resolve_input(self, dataset: Dataset) -> ResolvedInput:
        """Pick a serialization this engine can ingest, or fail with a clear message."""
        inp = dataset.resolve(self.input_formats)
        if inp is None:
            have = ", ".join(dataset.available_formats()) or "none present"
            raise EngineError(
                f"{self.name}: no compatible format for {dataset.name} "
                f"(accepts {', '.join(self.input_formats)}; available: {have})"
            )
        return inp

    def is_loaded(self, dataset: Dataset) -> bool:
        marker = self.storage_dir / _MARKER
        return marker.is_file() and marker.read_text().strip() == dataset.name

    def _mark_loaded(self, dataset: Dataset) -> None:
        (self.storage_dir / _MARKER).write_text(dataset.name)

    # ------------------------------------------------------------------ serving
    @abstractmethod
    def _start(self) -> None:
        """Launch the server process(es); populate ``self._server_procs``."""

    @abstractmethod
    def endpoint_url(self) -> str:
        """URL of the SPARQL query endpoint once started."""

    def start(self) -> str:
        # Never serve on a port something else still holds: a previous dataset's server can
        # outlive stop() and answer the health probe before vanishing (see docs/notes.md).
        # Only a fixed port can be held by our own previous server, and only there is
        # refusing better than trying — qLever has its own kill-whatever-holds-this-port.
        if not self._wait_port_free() and self.fixed_port is not None:
            raise EngineError(
                f"{self.name}: port {self.port} is still held by another process; "
                f"refusing to start (its server would be measured instead of ours)"
            )
        self._start()
        url = self.endpoint_url()
        self._wait_healthy(url, self.health_deadline_s)
        self._client = SparqlHttpClient(
            url, self.timeout_s, default_graph=self.default_graph,
            result_format=self.effective_result_format, chunk_rows=self.chunk_rows,
        )
        self._sampler = RssSampler(self._sample_rss, self.rss_sample_interval).start()
        return url

    def stop(self) -> None:
        if self._sampler is not None:
            self._sampler.stop()
            self._sampler = None
        if self._client is not None:
            self._client.close()
            self._client = None
        groups = [g for g in (self._pgid(p) for p in self._procs) if g is not None]
        for proc in reversed(self._procs):
            self._terminate(proc)
        self._procs.clear()
        self._server_procs.clear()
        self._release_port(groups)

    # ------------------------------------------------------------------ querying
    def query(self, sparql: str) -> QueryResult:
        if self._client is None:
            raise EngineError(f"{self.name}: engine not started")
        dead = next((p for p in self._procs if p.poll() is not None), None)
        if dead is not None:
            # One intelligible cause beats a wall of identical connection-refused
            # tracebacks, one per remaining rep. QueryError, not EngineError: that is what
            # the runner records per measurement — anything else aborts the whole run.
            raise QueryError(f"{self.name}: server exited (code {dead.returncode}); "
                             f"see {self.log_path}")
        return self._client.query(sparql)

    def reset_rss_peak(self) -> None:
        if self._sampler is not None:
            self._sampler.reset()

    def rss_peak(self) -> int:
        return self._sampler.peak if self._sampler is not None else 0

    def _sample_rss(self) -> int:
        return rss_of_tree(self._server_procs)

    # ------------------------------------------------------------------ helpers
    def _open_log(self):
        return open(self.log_path, "ab", buffering=0)

    def _spawn(self, cmd: list[str], *, env: dict | None = None, cwd: str | Path | None = None) -> subprocess.Popen:
        full_env = {**os.environ, **(env or {})}
        log = self._open_log()
        log.write(f"\n$ {' '.join(cmd)}\n".encode())
        proc = subprocess.Popen(
            cmd, cwd=str(cwd) if cwd else None, env=full_env,
            stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self._procs.append(proc)
        try:
            self._server_procs.append(psutil.Process(proc.pid))
        except psutil.NoSuchProcess:
            pass
        return proc

    @staticmethod
    def _pgid(proc: subprocess.Popen) -> int | None:
        """Process group of a spawned server, while it is still around to ask."""
        try:
            return os.getpgid(proc.pid)
        except OSError:
            return None

    @staticmethod
    def _terminate(proc: subprocess.Popen) -> None:
        if proc.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()

    def _release_port(self, pgids: list[int], grace_s: float = 10.0,
                      deadline_s: float = 60.0) -> None:
        """Wait for the port to actually come free after terminating the server.

        ``_terminate`` waits for the process *we* spawned, but a launcher that does not
        ``exec`` its server (``qendpoint.sh``) is killed by the SIGTERM long before the JVM
        it started has finished its shutdown hook — and that JVM is still listening. Without
        this wait ``stop()`` returns while the endpoint still answers, and the next dataset
        health-checks against a server about to disappear. Give the graceful shutdown
        ``grace_s``, then SIGKILL what is left of the process groups (guarded by the port
        still being open, so a recycled pgid is never signalled).
        """
        if not pgids:
            return  # nothing of ours to outlive us (qLever detaches its own server)
        start = time.perf_counter()
        killed = False
        while self._port_open():
            elapsed = time.perf_counter() - start
            if elapsed > deadline_s:
                return  # start() refuses a busy port, so it surfaces there, named
            if not killed and elapsed > grace_s:
                killed = True
                for pgid in pgids:
                    try:
                        os.killpg(pgid, signal.SIGKILL)
                    except OSError:
                        pass
            time.sleep(0.2)

    def _wait_healthy(self, url: str, deadline_s: float = 120.0) -> None:
        """Poll until the endpoint answers a trivial ASK, or a process dies."""
        end = time.perf_counter() + deadline_s
        probe = "ASK {}"
        while time.perf_counter() < end:
            for proc in self._procs:
                if proc.poll() is not None:
                    raise EngineError(
                        f"{self.name}: server exited early (code {proc.returncode}); "
                        f"see {self.log_path}"
                    )
            try:
                r = requests.post(
                    url, data=probe.encode(),
                    headers={"Accept": "application/sparql-results+json",
                             "Content-Type": "application/sparql-query"},
                    timeout=5,
                )
                if r.status_code == 200:
                    return
            except requests.RequestException:
                pass
            time.sleep(0.5)
        raise EngineError(f"{self.name}: endpoint {url} not healthy within {deadline_s}s")

    def _port_open(self) -> bool:
        """Whether something accepts connections on our port (ours or not)."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            return s.connect_ex(("127.0.0.1", self.port)) == 0

    def _wait_port_free(self, deadline_s: float = 30.0) -> bool:
        end = time.perf_counter() + deadline_s
        while self._port_open():
            if time.perf_counter() > end:
                return False
            time.sleep(0.2)
        return True
