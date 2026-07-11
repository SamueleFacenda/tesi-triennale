"""Resource tuning shared by all engines.

Big datasets need the engines configured for memory instead of running on defaults.
This computes a single memory *budget* (a fraction of system RAM, or an explicit
override) and derives the per-engine knobs from it. One engine runs at a time, so each
may claim the whole budget.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import psutil


@dataclass(frozen=True)
class Tuning:
    total_ram_gb: float
    cores: int
    budget_gb: int  # memory one engine may use

    @classmethod
    def detect(cls, mem_fraction: float = 0.5, memory_gb: float | None = None) -> "Tuning":
        total = psutil.virtual_memory().total / 1e9
        cores = os.cpu_count() or 4
        want = float(memory_gb) if memory_gb else total * mem_fraction
        # Always leave headroom for off-heap / native allocations (HDT build, mmap, OS
        # cache) so a big load isn't OOM-killed. A JVM -Xmx is only a ceiling, but native
        # memory on top of it is real.
        reserve = max(4.0, total * 0.25)
        budget = int(min(want, total - reserve))
        return cls(total_ram_gb=round(total, 1), cores=cores, budget_gb=max(1, budget))

    @property
    def load_workers(self) -> int:
        """Parallel loader workers — capped so many workers don't blow up memory."""
        return max(1, min(self.cores, 8))

    # -- JVM engines (fuseki, qendpoint, graphdb, stardog) --
    @property
    def xmx(self) -> str:
        return f"-Xmx{self.budget_gb}g"

    # -- Virtuoso: buffers are ~8 KiB each; ~85000 buffers per GB is OpenLink's rule. --
    @property
    def virtuoso_buffers(self) -> int:
        return int(self.budget_gb * 85000)

    @property
    def virtuoso_dirty_buffers(self) -> int:
        return int(self.virtuoso_buffers * 0.75)
