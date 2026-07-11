"""Progress reporters: a rich TUI and a plain-text fallback."""

from __future__ import annotations

import statistics

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

from .config import BenchConfig
from .engines.http_client import QueryResult
from .runner import Plan, Reporter


def _fmt_bytes(n: int | None) -> str:
    if not n:
        return "-"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PiB"


class RichReporter(Reporter):
    """Nested progress bars + per-query medians printed as they complete."""

    def __init__(self) -> None:
        self.console = Console()
        self.progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=self.console,
        )
        self._overall = None
        self._reps = None
        self._cur_walls: list[float] = []

    def run_start(self, cfg: BenchConfig, plan: Plan) -> None:
        self.progress.start()
        self._overall = self.progress.add_task("overall", total=max(1, plan.total_units))
        self._reps = self.progress.add_task("idle", total=1, visible=False)
        self.console.log(
            f"run [bold]{cfg.run_name}[/]: {len(plan.engines)} engines x "
            f"{len(plan.datasets)} datasets x {len(plan.queries)} queries "
            f"x {plan.repetitions} reps (warmup {plan.warmup})"
        )

    def engine_start(self, engine: str, n_datasets: int) -> None:
        self.console.log(f"[bold green]engine[/] {engine}")

    def engine_skip(self, engine: str, reason: str) -> None:
        self.console.log(f"[yellow]skip engine[/] {engine}: {reason}")

    def load_start(self, engine: str, dataset: str, fmt: str | None = None) -> None:
        self.progress.update(self._reps, description=f"{engine}/{dataset} loading", visible=False)
        self.console.log(f"  {engine}/{dataset}: loading{f' ({fmt})' if fmt else ''}…")

    def load_done(self, engine: str, dataset: str, load_time_s: float, disk_bytes: int) -> None:
        self.console.log(
            f"  {engine}/{dataset}: loaded in {load_time_s:.1f}s, disk {_fmt_bytes(disk_bytes)}"
        )

    def load_skip(self, engine: str, dataset: str) -> None:
        self.console.log(f"  {engine}/{dataset}: already loaded")

    def query_start(self, engine: str, dataset: str, query: str, reps: int) -> None:
        self._cur_walls = []
        self.progress.reset(self._reps, total=reps, visible=True,
                            description=f"{engine}/{dataset}/{query}")

    def measurement(self, engine: str, dataset: str, query: str, rep: int, res: QueryResult) -> None:
        self._cur_walls.append(res.wall_s)
        self.progress.advance(self._reps)

    def query_done(self, engine: str, dataset: str, query: str) -> None:
        self.progress.update(self._reps, visible=False)
        if self._cur_walls:
            med = statistics.median(self._cur_walls)
            self.console.log(f"    {query}: median {med * 1000:.1f} ms")

    def unit_done(self, n: int = 1) -> None:
        if self._overall is not None:
            self.progress.advance(self._overall, n)

    def error(self, msg: str) -> None:
        self.console.log(f"[red]error[/] {msg}")

    def log(self, msg: str) -> None:
        self.console.log(msg)

    def run_done(self) -> None:
        if self._overall is not None:
            self.progress.advance(self._overall, 0)
        self.progress.stop()
        self.console.log("[bold green]done[/]")


class PlainReporter(Reporter):
    """Line-oriented output for non-TTY / --no-tui runs."""

    def engine_start(self, engine: str, n_datasets: int) -> None:
        print(f"engine {engine}", flush=True)

    def engine_skip(self, engine: str, reason: str) -> None:
        print(f"skip engine {engine}: {reason}", flush=True)

    def load_start(self, engine: str, dataset: str, fmt: str | None = None) -> None:
        print(f"  {engine}/{dataset}: loading{f' ({fmt})' if fmt else ''}", flush=True)

    def load_done(self, engine: str, dataset: str, load_time_s: float, disk_bytes: int) -> None:
        print(f"  {engine}/{dataset}: loaded in {load_time_s:.1f}s, disk {_fmt_bytes(disk_bytes)}", flush=True)

    def load_skip(self, engine: str, dataset: str) -> None:
        print(f"  {engine}/{dataset}: already loaded", flush=True)

    def query_start(self, engine: str, dataset: str, query: str, reps: int) -> None:
        print(f"  {engine}/{dataset}/{query}: {reps} reps", flush=True)

    def query_done(self, engine: str, dataset: str, query: str) -> None:
        pass

    def error(self, msg: str) -> None:
        print(f"error: {msg}", flush=True)

    def log(self, msg: str) -> None:
        print(msg, flush=True)
