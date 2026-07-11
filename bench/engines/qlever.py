"""QLever — driven through the ``qlever`` control CLI (qlever-control).

QLever needs its server binaries (``IndexBuilderMain`` / ``ServerMain``). When they are
on PATH we run natively; otherwise qlever-control falls back to its docker image. QLever
reads only Turtle / N-Triples / N-Quads, so RDF/XML datasets are rejected.
"""

from __future__ import annotations

import shutil

from ..datasets import Dataset
from ..metrics import run_sampled
from .base import AbstractEngine, EngineError, LoadResult, which
from .registry import register_engine

_FORMAT = {"ttl": "ttl", "nt": "nt"}  # rdfxml unsupported by QLever


def _qlever_system() -> str | None:
    if which("ServerMain") and which("IndexBuilderMain"):
        return "native"
    if which("docker") or which("podman"):
        return "docker"
    return None


@register_engine("qlever")
class QleverEngine(AbstractEngine):
    required_binaries = ["qlever"]
    _NAME = "bench"

    @classmethod
    def available(cls) -> tuple[bool, str]:
        ok, reason = super().available()
        if not ok:
            return ok, reason
        if _qlever_system() is None:
            return False, "needs native ServerMain/IndexBuilderMain or docker"
        return True, "ok"

    def _write_qleverfile(self, dataset: Dataset) -> str:
        fmt = _FORMAT.get(dataset.fmt)
        if fmt is None:
            raise EngineError(f"qlever cannot load {dataset.fmt!r} (Turtle/N-Triples only)")
        system = _qlever_system()
        # qlever-control requires a *relative* INPUT_FILES glob (it rejects absolute
        # patterns), and in docker mode the file must live inside the mounted working dir
        # (a symlink's target would be invisible in the container). A hardlink puts the
        # real content there with zero copy when on the same filesystem.
        link_name = f"input.{fmt}"
        link = self.storage_dir / link_name
        if link.is_symlink() or link.exists():
            link.unlink()
        try:
            import os
            os.link(dataset.path, link)          # hardlink, no copy
        except OSError:
            link.symlink_to(dataset.path)        # cross-device: fall back (native only)
        budget = self.tuning.budget_gb
        content = f"""[data]
NAME = {self._NAME}
DESCRIPTION = kbench {dataset.name}

[index]
INPUT_FILES = {link_name}
CAT_INPUT_FILES = cat {link_name}
FORMAT = {fmt}
STXXL_MEMORY = {budget}G
PARALLEL_PARSING = true

[server]
PORT = {self.port}
ACCESS_TOKEN = bench_token
MEMORY_FOR_QUERIES = {budget}G
CACHE_MAX_SIZE = {max(1, budget // 3)}G

[runtime]
SYSTEM = {system}
IMAGE = docker.io/adfreiburg/qlever:latest
"""
        (self.storage_dir / "Qleverfile").write_text(content)
        return content

    def load(self, dataset: Dataset) -> LoadResult:
        # wipe any previous index files (NAME.*) in the storage dir
        for p in self.storage_dir.glob(f"{self._NAME}.*"):
            p.unlink()
        self._write_qleverfile(dataset)
        res = run_sampled(
            ["qlever", "index", "--overwrite-existing"],
            cwd=self.storage_dir,
        )
        if res.returncode != 0:
            raise EngineError(f"qlever index failed:\n{res.stdout[-2000:]}\n{res.stderr[-2000:]}")
        self._mark_loaded(dataset)
        return LoadResult(res.elapsed_s, res.peak_rss_bytes)

    def _start(self) -> None:
        # qlever-control detaches the server. Pass --port explicitly (the Qleverfile's
        # port may be stale on resume) and kill any leftover server on that port.
        res = run_sampled(
            ["qlever", "start", "--port", str(self.port), "--kill-existing-with-same-port"],
            cwd=self.storage_dir,
        )
        if res.returncode != 0:
            raise EngineError(f"qlever start failed:\n{res.stdout[-2000:]}\n{res.stderr[-2000:]}")
        self._attach_server_procs()

    def _attach_server_procs(self) -> None:
        # qlever start detaches; find the ServerMain / docker process bound to our port.
        import psutil
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                cmd = " ".join(proc.info["cmdline"] or [])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            if str(self.port) in cmd and ("ServerMain" in cmd or "qlever" in cmd or "adfreiburg" in cmd):
                self._server_procs.append(proc)

    def stop(self) -> None:
        # ask qlever-control to stop its (detached) server first
        try:
            run_sampled(["qlever", "stop"], cwd=self.storage_dir, timeout=30)
        except Exception:
            pass
        super().stop()

    def endpoint_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"
