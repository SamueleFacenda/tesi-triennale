"""Benchmark orchestration.

Strictly sequential: one engine started at a time, one query at a time — so each query
gets the machine's full resources and there is no cross-query interference. Warmup runs
precede each measured batch to avoid cold-cache bias. A serialization/connection-overhead
baseline (a trivial 1-row query) is measured per engine+dataset.

Everything is checkpointed through :class:`BenchStore`, so a run is resumable: already
completed measurements and already loaded datasets are skipped.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import datasets as datasets_mod
from . import queries as queries_mod
from . import engines as engines_mod
from .config import BenchConfig
from .engines.base import AbstractEngine, EngineError
from .engines.http_client import QueryError, QueryResult
from .metrics import dir_size
from .store import BASELINE, BenchStore
from .tuning import Tuning

# tiny query used to measure the connection + serialization floor
_BASELINE_SPARQL = "SELECT ?s WHERE { ?s ?p ?o } LIMIT 1\n"


class Reporter:
    """No-op progress reporter; the TUI subclasses this."""

    def run_start(self, cfg: BenchConfig, plan: "Plan") -> None: ...
    def engine_start(self, engine: str, n_datasets: int) -> None: ...
    def engine_skip(self, engine: str, reason: str) -> None: ...
    def load_start(self, engine: str, dataset: str, fmt: str | None = None) -> None: ...
    def load_done(self, engine: str, dataset: str, load_time_s: float, disk_bytes: int) -> None: ...
    def load_skip(self, engine: str, dataset: str) -> None: ...
    def query_start(self, engine: str, dataset: str, query: str, reps: int) -> None: ...
    def measurement(self, engine: str, dataset: str, query: str, rep: int, res: QueryResult) -> None: ...
    def query_done(self, engine: str, dataset: str, query: str) -> None: ...
    def unit_done(self, n: int = 1) -> None: ...  # advance the overall progress bar
    def error(self, msg: str) -> None: ...
    def log(self, msg: str) -> None: ...
    def run_done(self) -> None: ...


@dataclass
class Plan:
    engines: list[str]
    datasets: list[str]
    queries: list[str]
    repetitions: int
    warmup: int
    total_units: int  # engines x applicable queries (drives the overall progress bar)


class BenchRunner:
    def __init__(self, cfg: BenchConfig, store: BenchStore, reporter: Reporter | None = None):
        self.cfg = cfg
        self.store = store
        self.reporter = reporter or Reporter()
        self.tuning = Tuning.detect(mem_fraction=cfg.mem_fraction, memory_gb=cfg.memory_gb)

    def plan(self) -> Plan:
        units = len(self.cfg.engines) * sum(
            len(self._applicable_for(d)) for d in self.cfg.datasets
        )
        return Plan(
            engines=self.cfg.engines,
            datasets=self.cfg.datasets,
            queries=self.cfg.resolved_queries(),
            repetitions=self.cfg.repetitions,
            warmup=self.cfg.warmup,
            total_units=units,
        )

    def _applicable_for(self, dataset_name: str) -> list:
        """Queries that apply to a dataset (deterministic — no engine/file dependency)."""
        q_objs = [queries_mod.get(q) for q in self.cfg.resolved_queries()]
        return queries_mod.applicable(q_objs, datasets_mod.get(dataset_name))

    def _engine_units(self) -> int:
        return sum(len(self._applicable_for(d)) for d in self.cfg.datasets)

    def run(self) -> None:
        plan = self.plan()
        self.reporter.run_start(self.cfg, plan)
        self.reporter.log(
            f"tuning: {self.tuning.budget_gb} GB budget/engine "
            f"({self.tuning.total_ram_gb} GB RAM, {self.tuning.cores} cores)"
        )
        for engine_name in plan.engines:
            self._run_engine(engine_name, plan)
        self.reporter.run_done()

    # ------------------------------------------------------------------ per engine
    def _run_engine(self, engine_name: str, plan: Plan) -> None:
        try:
            cls = engines_mod.get(engine_name)
        except KeyError as e:
            self.reporter.engine_skip(engine_name, str(e))
            self.reporter.unit_done(self._engine_units())
            return
        ok, reason = cls.available()
        if not ok:
            self.reporter.engine_skip(engine_name, reason)
            self.reporter.unit_done(self._engine_units())
            return

        fmt = self.cfg.result_format or cls.result_format
        self.reporter.engine_start(engine_name, len(plan.datasets))
        self.reporter.log(f"  {engine_name}: result format {fmt}")
        try:
            # a throwaway instance is fine for provisioning (pull image, etc.)
            cls(self.store.storage_dir(engine_name, "_provision"), tuning=self.tuning).provision()
        except Exception as e:  # noqa: BLE001
            self.reporter.engine_skip(engine_name, f"provision failed: {e}")
            self.reporter.unit_done(self._engine_units())
            return

        for dataset_name in plan.datasets:
            self._run_dataset(cls, engine_name, dataset_name, plan)

    # ------------------------------------------------------------------ per dataset
    def _run_dataset(self, cls: type[AbstractEngine], engine_name: str,
                     dataset_name: str, plan: Plan) -> None:
        ds = datasets_mod.get(dataset_name)
        applicable = self._applicable_for(dataset_name)
        if not ds.available():
            paths = ", ".join(ds.formats.values())
            self.reporter.error(f"{engine_name}/{dataset_name}: file missing ({paths}); skipped")
            self.reporter.unit_done(len(applicable))
            return

        storage = self.store.storage_dir(engine_name, dataset_name)
        engine = cls(storage, timeout_s=self.cfg.timeout_s, tuning=self.tuning,
                     result_format=self.cfg.result_format)
        resolved = ds.resolve(engine.input_formats)
        fmt = resolved.fmt if resolved else None

        # ---- load (skip if already loaded and intact) ----
        if self.store.load_ok(engine_name, dataset_name) and engine.is_loaded(ds):
            self.reporter.load_skip(engine_name, dataset_name)
        else:
            self.reporter.load_start(engine_name, dataset_name, fmt)
            try:
                lr = engine.load(ds)
                disk = dir_size(storage)
                self.store.save_load(
                    engine_name, dataset_name, fmt=fmt, load_time_s=lr.load_time_s,
                    disk_bytes=disk, peak_rss_bytes=lr.peak_rss_bytes, status="ok",
                )
                self.reporter.load_done(engine_name, dataset_name, lr.load_time_s, disk)
            except Exception as e:  # noqa: BLE001
                self.store.save_load(
                    engine_name, dataset_name, fmt=fmt, load_time_s=None, disk_bytes=None,
                    peak_rss_bytes=None, status="error", error=str(e),
                )
                self.reporter.error(f"{engine_name}/{dataset_name}: load failed: {e}")
                self.reporter.unit_done(len(applicable))
                return

        # ---- resume shortcut: everything already measured? ----
        pending = [
            q for q in applicable
            if self.store.measured_count(engine_name, dataset_name, q.name) < plan.repetitions
        ]
        if not pending:
            self.reporter.log(f"{engine_name}/{dataset_name}: all queries complete, skipping")
            self.reporter.unit_done(len(applicable))
            return

        # ---- serve + query ----
        try:
            engine.start()
        except EngineError as e:
            self.reporter.error(f"{engine_name}/{dataset_name}: start failed: {e}")
            engine.stop()
            self.reporter.unit_done(len(applicable))
            return

        try:
            self._baseline(engine, engine_name, dataset_name, plan)
            for q in applicable:
                self._run_query(engine, engine_name, dataset_name, q, plan)
        finally:
            engine.stop()

    # ------------------------------------------------------------------ per query
    def _baseline(self, engine: AbstractEngine, engine_name: str, dataset_name: str, plan: Plan) -> None:
        for _ in range(plan.warmup):
            try:
                engine.query(_BASELINE_SPARQL)
            except QueryError:
                break
        for rep in range(plan.repetitions):
            if self.store.measured_ok(engine_name, dataset_name, BASELINE, rep):
                continue
            self._measure_once(engine, engine_name, dataset_name, BASELINE, _BASELINE_SPARQL, rep)

    def _run_query(self, engine: AbstractEngine, engine_name: str, dataset_name: str,
                   query: queries_mod.Query, plan: Plan) -> None:
        ds = datasets_mod.get(dataset_name)
        sparql = query.render(ds)
        self.reporter.query_start(engine_name, dataset_name, query.name, plan.repetitions)

        # warmup (always re-run: the server was just started, cache is cold)
        for _ in range(plan.warmup):
            try:
                engine.query(sparql)
            except QueryError:
                break

        for rep in range(plan.repetitions):
            if self.store.measured_ok(engine_name, dataset_name, query.name, rep):
                continue
            self._measure_once(engine, engine_name, dataset_name, query.name, sparql, rep)
        self.reporter.query_done(engine_name, dataset_name, query.name)
        self.reporter.unit_done()

    def _measure_once(self, engine: AbstractEngine, engine_name: str, dataset_name: str,
                      query_name: str, sparql: str, rep: int) -> None:
        engine.reset_rss_peak()
        try:
            res = engine.query(sparql)
        except QueryError as e:
            self.store.save_measurement(
                engine_name, dataset_name, query_name, rep, warmup=False,
                wall_s=None, server_s=None, rows=None, bytes_=None,
                peak_rss_bytes=engine.rss_peak(), status="error", error=str(e),
            )
            self.reporter.error(f"{engine_name}/{dataset_name}/{query_name} rep{rep}: {e}")
            return
        self.store.save_measurement(
            engine_name, dataset_name, query_name, rep, warmup=False,
            wall_s=res.wall_s, server_s=res.server_s, rows=res.rows, bytes_=res.bytes,
            peak_rss_bytes=engine.rss_peak(), status="ok",
        )
        self.reporter.measurement(engine_name, dataset_name, query_name, rep, res)
