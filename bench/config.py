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
    # (the runner still reserves ~25% headroom for native/off-heap allocations)
    mem_fraction: float = 0.5
    memory_gb: float | None = None
    # force one SPARQL result format for every engine (json/xml/csv/tsv); None = each
    # engine's own fastest default. Useful for a uniform-format control run.
    result_format: str | None = None
    engines: list[str] = field(default_factory=list)
    datasets: list[str] = field(default_factory=list)
    # engine -> datasets it must not be run on, for pairs known to be unaffordable rather
    # than merely slow (see docs/configuration.md). Everything else still runs.
    exclude: dict[str, list[str]] = field(default_factory=dict)
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

    def _resolve_aliases(self) -> None:
        """Map dataset keys recorded by an older run onto their current names, so a config
        snapshot written before a rename still loads (see datasets.DATASET_ALIASES)."""
        self.datasets = [datasets_mod.canonical(d) for d in self.datasets]
        self.exclude = {e: [datasets_mod.canonical(d) for d in skipped]
                        for e, skipped in self.exclude.items()}

    def validate(self) -> None:
        self._resolve_aliases()
        if not self.engines:
            raise ValueError("config: 'engines' must list at least one engine")
        if not self.datasets:
            raise ValueError("config: 'datasets' must list at least one dataset")
        for name in self.datasets:
            datasets_mod.get(name)  # raises on unknown
        for engine, skipped in self.exclude.items():
            if engine not in self.engines:
                raise ValueError(f"config: 'exclude' names engine {engine!r}, not in 'engines'")
            for name in skipped:
                datasets_mod.get(name)  # raises on unknown
        for name in self.resolved_queries():
            queries_mod.get(name)   # raises on unknown
        if self.repetitions < 1:
            raise ValueError("config: 'repetitions' must be >= 1")
        if self.warmup < 0:
            raise ValueError("config: 'warmup' must be >= 0")
        if self.result_format is not None:
            from .engines.http_client import FORMATS
            if self.result_format not in FORMATS:
                raise ValueError(f"config: 'result_format' must be one of {', '.join(FORMATS)}")

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
            "result_format": self.result_format,
            "engines": self.engines,
            "datasets": self.datasets,
            "queries": self.queries,
            "exclude": self.exclude,
        }
