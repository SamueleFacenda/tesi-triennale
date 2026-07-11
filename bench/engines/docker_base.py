"""Shared base for engines that run inside a container (docker or podman).

RSS is read from ``docker stats`` (the engine runs in the container, so the host-side
process tree is just the CLI). Subclasses provide the image, container port, the command
to start the server, and how to bulk-load a dataset.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from typing import ClassVar

from .base import AbstractEngine, EngineError, which


def _runtime() -> str | None:
    return which("docker") or which("podman")


def _bytes_from_stats(mem: str) -> int:
    """Parse the used side of docker stats MemUsage like '1.5GiB / 16GiB'."""
    used = mem.split("/")[0].strip()
    units = {"B": 1, "KIB": 1024, "MIB": 1024**2, "GIB": 1024**3, "TIB": 1024**4,
             "KB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4}
    num, unit = "", ""
    for ch in used:
        if ch.isdigit() or ch == ".":
            num += ch
        else:
            unit += ch
    try:
        return int(float(num) * units.get(unit.strip().upper(), 1))
    except ValueError:
        return 0


class DockerEngine(AbstractEngine):
    image: ClassVar[str] = ""
    container_port: ClassVar[int] = 0
    rss_sample_interval: ClassVar[float] = 2.0  # docker stats is slow; sample sparingly
    # env var naming a license file that must exist for this engine to run (or None)
    license_env: ClassVar[str | None] = None

    @classmethod
    def available(cls) -> tuple[bool, str]:
        rt = _runtime()
        if rt is None:
            return False, "no docker/podman on PATH"
        try:
            subprocess.run([rt, "info"], capture_output=True, timeout=10, check=True)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            return False, "docker/podman daemon not reachable"
        if cls.license_env:
            import os
            path = os.environ.get(cls.license_env)
            if not path or not Path(path).is_file():
                return False, f"license file not found (set ${cls.license_env})"
        return True, "ok"

    @property
    def container(self) -> str:
        return f"kbench_{self.name}_{self.port}"

    def _rt(self) -> str:
        rt = _runtime()
        if rt is None:
            raise EngineError(f"{self.name}: no docker/podman")
        return rt

    def _docker(self, *args: str, check: bool = True, timeout: float | None = 60) -> subprocess.CompletedProcess:
        proc = subprocess.run([self._rt(), *args], capture_output=True, text=True, timeout=timeout)
        if check and proc.returncode != 0:
            raise EngineError(f"{self.name}: `docker {' '.join(args)}` failed:\n{proc.stderr[-2000:]}")
        return proc

    def provision(self) -> None:
        self._docker("pull", self.image, timeout=1800)

    def _dump_container_log(self) -> None:
        """Append the container's stdout/stderr to the engine log (before removal)."""
        logs = self._docker("logs", self.container, check=False)
        try:
            with open(self.log_path, "ab") as f:
                f.write(f"\n=== docker logs {self.container} ===\n".encode())
                f.write((logs.stdout + logs.stderr).encode("utf-8", "replace"))
        except OSError:
            pass

    def _rm_container(self) -> None:
        self._docker("rm", "-f", self.container, check=False)

    def _wipe(self, path: Path) -> None:
        """Remove a data dir, even when the container wrote it as root.

        Container engines persist files owned by the container's uid, which the host user
        often cannot delete. Fall back to a throwaway busybox container (which runs as
        root) to remove them, so re-load / --fresh work.
        """
        if not path.exists():
            return
        try:
            shutil.rmtree(path)
        except (PermissionError, OSError):
            self._docker(
                "run", "--rm", "-v", f"{path.parent}:/wipe", "busybox",
                "rm", "-rf", f"/wipe/{path.name}", check=False, timeout=120,
            )

    def _run_container(self, extra: list[str], cmd: list[str] | None = None) -> None:
        """Start a detached container publishing container_port -> self.port."""
        self._rm_container()
        args = [
            "run", "-d", "--name", self.container,
            "-p", f"127.0.0.1:{self.port}:{self.container_port}",
            *extra, self.image,
        ]
        if cmd:
            args += cmd
        self._docker(*args)

    def _sample_rss(self) -> int:
        try:
            proc = subprocess.run(
                [self._rt(), "stats", "--no-stream", "--format", "{{.MemUsage}}", self.container],
                capture_output=True, text=True, timeout=10,
            )
        except (subprocess.TimeoutExpired, OSError):
            return 0
        if proc.returncode != 0 or not proc.stdout.strip():
            return 0
        return _bytes_from_stats(proc.stdout.strip())

    def _wait_container_log(self, needle: str, deadline_s: float = 180.0) -> None:
        end = time.perf_counter() + deadline_s
        while time.perf_counter() < end:
            logs = self._docker("logs", self.container, check=False).stdout \
                + self._docker("logs", self.container, check=False).stderr
            if needle in logs:
                return
            insp = self._docker("inspect", "-f", "{{.State.Running}}", self.container, check=False)
            if insp.stdout.strip() == "false":
                raise EngineError(f"{self.name}: container exited early; see `docker logs {self.container}`")
            time.sleep(1.0)
        raise EngineError(f"{self.name}: '{needle}' not seen in container logs within {deadline_s}s")

    def stop(self) -> None:
        if self._sampler is not None:
            self._sampler.stop()
            self._sampler = None
        if self._client is not None:
            self._client.close()
            self._client = None
        self._rm_container()
