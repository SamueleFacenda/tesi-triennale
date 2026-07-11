"""Benchmark configuration loaded from a TOML file."""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import datasets as datasets_mod
from . import queries as queries_mod


@dataclass
class BenchConfig:
    run_name: str = "default"
    repetitions: int = 10
    warmup: int = 2
    timeout_s: float = 300.0
    output_dir: str = "runs"
    # memory budget per engine: a fraction of system RAM, or an explicit override in GB
    mem_fraction: float = 0.75
    memory_gb: float | None = None
    engines: list[str] = field(default_factory=list)
    datasets: list[str] = field(default_factory=list)
    # list of query names, or the literal "all"
    queries: list[str] | str = "all"

    @classmethod
    def load(cls, path: str | Path) -> "BenchConfig":
        with open(path, "rb") as f:
            raw = tomllib.load(f)
        known = {f.name for f in cls.__dataclass_fields__.values()}
        unknown = set(raw) - known
        if unknown:
            raise ValueError(f"unknown config keys: {', '.join(sorted(unknown))}")
        cfg = cls(**raw)
        cfg.validate()
        return cfg

    def resolved_queries(self) -> list[str]:
        if self.queries == "all":
            return [q.name for q in queries_mod.all_queries()]
        return list(self.queries)

    def validate(self) -> None:
        if not self.engines:
            raise ValueError("config: 'engines' must list at least one engine")
        if not self.datasets:
            raise ValueError("config: 'datasets' must list at least one dataset")
        for name in self.datasets:
            datasets_mod.get(name)  # raises on unknown
        for name in self.resolved_queries():
            queries_mod.get(name)   # raises on unknown
        if self.repetitions < 1:
            raise ValueError("config: 'repetitions' must be >= 1")
        if self.warmup < 0:
            raise ValueError("config: 'warmup' must be >= 0")

    @classmethod
    def from_dict(cls, raw: dict) -> "BenchConfig":
        cfg = cls(**raw)
        cfg.validate()
        return cfg

    def save_snapshot(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def load_snapshot(cls, path: str | Path) -> "BenchConfig":
        return cls.from_dict(json.loads(Path(path).read_text()))

    def to_dict(self) -> dict:
        return {
            "run_name": self.run_name,
            "repetitions": self.repetitions,
            "warmup": self.warmup,
            "timeout_s": self.timeout_s,
            "output_dir": self.output_dir,
            "mem_fraction": self.mem_fraction,
            "memory_gb": self.memory_gb,
            "engines": self.engines,
            "datasets": self.datasets,
            "queries": self.queries,
        }
