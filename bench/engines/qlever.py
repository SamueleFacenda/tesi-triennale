"""QLever — driven through the ``qlever`` control CLI (qlever-control).

QLever needs its server binaries (``IndexBuilderMain`` / ``ServerMain``). When they are
on PATH we run natively; otherwise qlever-control falls back to its docker image. QLever
reads only Turtle / N-Triples / N-Quads, so RDF/XML datasets are rejected.
"""

from __future__ import annotations

import shutil
import subprocess

from ..datasets import Dataset, ResolvedInput
from ..metrics import run_sampled
from .base import AbstractEngine, EngineError, LoadResult, which
from .registry import register_engine


def _longest_line_bytes(path) -> int:
    """Length of the longest line, in bytes. Drives PARSER_BUFFER_SIZE (see below)."""
    longest = since_newline = 0
    with open(path, "rb") as fh:
        while chunk := fh.read(1 << 24):
            parts = chunk.split(b"\n")
            if len(parts) == 1:
                since_newline += len(chunk)
                continue
            longest = max(longest, since_newline + len(parts[0]),
                          max(map(len, parts[1:-1]), default=0))
            since_newline = len(parts[-1])
    return max(longest, since_newline)


def _qlever_system() -> str | None:
    if which("ServerMain") and which("IndexBuilderMain"):
        return "native"
    if which("docker") or which("podman"):
        return "docker"
    return None


@register_engine("qlever")
class QleverEngine(AbstractEngine):
    required_binaries = ["qlever"]
    input_formats = ["ttl", "nt"]  # QLever does not read RDF/XML
    _NAME = "bench"
    # `qlever start` blocks until the server answers, with no timeout of its own: a
    # server that crash-loops (docker --restart=unless-stopped) would hang forever.
    _START_TIMEOUT_S = 300.0

    def _index_complete(self) -> bool:
        """Whether a full index is on disk.

        Needed because qlever-control pipes IndexBuilderMain through ``tee``, so the
        shell reports ``tee``'s exit status and `qlever index` returns 0 even when the
        build aborted. The metadata alone is not enough — it appears partway through —
        so require every permutation the server will open.
        """
        names = [f"{self._NAME}.index.{p}"
                 for p in ("spo", "sop", "osp", "ops", "pso", "pos")]
        names.append(f"{self._NAME}.meta-data.json")
        return all((self.storage_dir / n).exists() for n in names)

    @classmethod
    def available(cls) -> tuple[bool, str]:
        ok, reason = super().available()
        if not ok:
            return ok, reason
        if _qlever_system() is None:
            return False, "needs native ServerMain/IndexBuilderMain or docker"
        return True, "ok"

    def _write_qleverfile(self, dataset: Dataset, inp: ResolvedInput) -> str:
        fmt = inp.fmt  # resolver guarantees ttl or nt
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
            os.link(inp.path, link)              # hardlink, no copy
        except OSError:
            link.symlink_to(inp.path)            # cross-device: fall back (native only)
        budget = self.tuning.budget_gb
        # QLever's parser reads in fixed-size batches and every batch but the last must
        # contain an end-of-statement, else the whole build aborts. Turtle written by
        # ontology editors can put an enormous predicate-object list on one line
        # (torre_modena.ttl has a single 288 MB line), so size the buffer to the input:
        # serial parsing needs a newline in the batch, parallel needs a full statement,
        # hence the extra headroom there. N-Triples is one statement per line, so it can
        # always parse in parallel on the 10M default.
        parallel = "true" if fmt == "nt" else "false"
        longest_mb = _longest_line_bytes(inp.path) // (1 << 20) + 1
        buffer_mb = max(10, longest_mb * (4 if parallel == "true" else 2))
        content = f"""[data]
NAME = {self._NAME}
DESCRIPTION = kbench {dataset.name}

[index]
INPUT_FILES = {link_name}
CAT_INPUT_FILES = cat {link_name}
FORMAT = {fmt}
STXXL_MEMORY = {budget}G
PARALLEL_PARSING = {parallel}
PARSER_BUFFER_SIZE = {buffer_mb}M

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
        inp = self.resolve_input(dataset)
        # wipe any previous index files (NAME.*) in the storage dir
        for p in self.storage_dir.glob(f"{self._NAME}.*"):
            p.unlink()
        self._write_qleverfile(dataset, inp)
        res = run_sampled(
            ["qlever", "index", "--overwrite-existing"],
            cwd=self.storage_dir, log_path=self.log_path,
        )
        if not res.ok or not self._index_complete():
            raise EngineError(f"qlever index failed ({res.describe_failure()}); "
                              f"see {self.log_path}\n{res.output[-1500:]}")
        self._mark_loaded(dataset)
        return LoadResult(res.elapsed_s, res.peak_rss_bytes)

    def is_loaded(self, dataset: Dataset) -> bool:
        return super().is_loaded(dataset) and self._index_complete()

    def _start(self) -> None:
        # qlever-control detaches the server. Pass --port explicitly (the Qleverfile's
        # port may be stale on resume) and kill any leftover server on that port.
        try:
            res = run_sampled(
                ["qlever", "start", "--port", str(self.port),
                 "--kill-existing-with-same-port"],
                cwd=self.storage_dir, log_path=self.log_path,
                timeout=self._START_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            raise EngineError(
                f"qlever start did not come up within {self._START_TIMEOUT_S:.0f}s "
                f"(it waits for the server forever); see {self.log_path}"
            ) from None
        if not res.ok:
            raise EngineError(f"qlever start failed ({res.describe_failure()}); "
                              f"see {self.log_path}\n{res.output[-1500:]}")
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
