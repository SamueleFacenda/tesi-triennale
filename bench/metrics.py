"""Timing, memory sampling and disk-usage helpers."""

from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import psutil


def dir_size(path: str | Path) -> int:
    """Total size in bytes of everything under ``path`` (0 if missing)."""
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def rss_of_tree(procs: list[psutil.Process]) -> int:
    """Summed RSS (bytes) of the given processes and all their descendants."""
    seen: dict[int, psutil.Process] = {}
    for p in procs:
        try:
            seen[p.pid] = p
            for c in p.children(recursive=True):
                seen[c.pid] = c
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    total = 0
    for p in seen.values():
        try:
            total += p.memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return total


class RssSampler:
    """Background sampler tracking the peak of a ``sample_fn`` (bytes).

    ``sample_fn`` returns the current total RSS. Native engines pass a psutil-based
    function over the server process tree; docker engines pass a ``docker stats`` reader.
    ``reset`` re-bases the peak to the latest sample (used to isolate a single query).
    """

    def __init__(self, sample_fn: Callable[[], int], interval: float = 0.05):
        self._sample = sample_fn
        self._interval = interval
        self._peak = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                v = self._sample()
            except Exception:
                v = 0
            with self._lock:
                if v > self._peak:
                    self._peak = v
            self._stop.wait(self._interval)

    def start(self) -> "RssSampler":
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def reset(self) -> None:
        try:
            v = self._sample()
        except Exception:
            v = 0
        with self._lock:
            self._peak = v

    @property
    def peak(self) -> int:
        with self._lock:
            return self._peak

    def stop(self) -> int:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        return self.peak


@dataclass
class SubprocResult:
    returncode: int
    elapsed_s: float
    peak_rss_bytes: int
    output: str            # tail of the combined stdout+stderr

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def killed_by_signal(self) -> int | None:
        # Popen reports a signal death as a negative return code.
        return -self.returncode if self.returncode is not None and self.returncode < 0 else None

    def describe_failure(self) -> str:
        sig = self.killed_by_signal
        if sig is not None:
            hint = " (SIGKILL — likely out of memory)" if sig == 9 else ""
            return f"process killed by signal {sig}{hint}"
        return f"exit code {self.returncode}"


def _tail(path: Path, n_bytes: int = 4000) -> str:
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            if size > n_bytes:
                f.seek(size - n_bytes)
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def run_sampled(
    cmd: list[str],
    *,
    cwd: str | Path | None = None,
    env: dict | None = None,
    timeout: float | None = None,
    interval: float = 0.05,
    log_path: str | Path | None = None,
) -> SubprocResult:
    """Run ``cmd`` to completion, timing it and tracking its peak RSS tree.

    Used for the loading steps. When ``log_path`` is given the child's combined
    stdout+stderr is streamed to that file (so the engine's own loader log survives even
    if the child is OOM-killed); otherwise it is captured in a pipe.
    """
    full_env = {**os.environ, **(env or {})}
    log_file = None
    if log_path is not None:
        log_path = Path(log_path)
        log_file = open(log_path, "ab", buffering=0)
        log_file.write(f"\n$ {' '.join(cmd)}\n".encode())
        stdout_target: object = log_file
        stderr_target: object = subprocess.STDOUT
    else:
        stdout_target = subprocess.PIPE
        stderr_target = subprocess.STDOUT

    start = time.perf_counter()
    proc = subprocess.Popen(
        cmd, cwd=str(cwd) if cwd else None, env=full_env,
        stdout=stdout_target, stderr=stderr_target,
        text=(log_file is None),
    )
    try:
        ps = psutil.Process(proc.pid)
    except psutil.NoSuchProcess:
        ps = None

    sampler = RssSampler(lambda: rss_of_tree([ps]) if ps else 0, interval)
    sampler.start()
    piped = ""
    try:
        if log_file is None:
            piped, _ = proc.communicate(timeout=timeout)
        else:
            proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        sampler.stop()
        if log_file is not None:
            log_file.close()
        raise
    peak = sampler.stop()
    if log_file is not None:
        log_file.close()
        output = _tail(log_path)
    else:
        output = piped or ""
    return SubprocResult(
        returncode=proc.returncode,
        elapsed_s=time.perf_counter() - start,
        peak_rss_bytes=peak,
        output=output,
    )
