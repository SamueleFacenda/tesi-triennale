"""Shape a finished run into the grid the thesis figures and tables are drawn from.

`report.summarize()` already aggregates every (engine, dataset, query) it *has* rows for.
What the thesis needs on top of that is the complete grid, including the cells the runner
never wrote a row for: engines excluded by config, datasets whose load failed, queries that
only make sense on one dataset. Those absences are meaningful results and must be labelled,
not dropped.

Everything user-facing (Italian labels, footnote wording) lives here or in
:mod:`bench.thesis_tables`; the rest of the harness stays language-neutral.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .config import BenchConfig
from .report import load_rows, summarize
from .store import BASELINE, BenchStore

# Datasets in the order the thesis presents them (by size) and the names it gives them,
# see thesis/chapters/benchmark.tex "Dati".
DATASET_ORDER = ["ytu3d", "torre_modena", "nettuno", "colosseo"]
DATASET_LABEL = {
    "ytu3d": "Piccolo",
    "torre_modena": "Medio",
    "nettuno": "Medio 2",
    "colosseo": "Grande",
}

# Queries in registry order, labelled as in benchmark.tex "Query".
QUERY_ORDER = [
    "select_all",
    "scalar_max_x",
    "select_points_in_object",
    "instance_segmentation",
    "chained_segmentation",
    "max_lod",
    "taxonomical_hierarchy",
    "avg_height",
]
QUERY_LABEL = {
    "select_all": "Seleziona tutto",
    "scalar_max_x": "Scalare",
    "select_points_in_object": "Punti nell'oggetto",
    "instance_segmentation": "Segmentazione delle istanze",
    "chained_segmentation": "Segmentazione tassonomica",
    "max_lod": "LOD massimo",
    "taxonomical_hierarchy": "Gerarchia tassonomica",
    "avg_height": "Altezza media",
    BASELINE: "Latenza base",
}

ENGINE_LABEL = {
    "fuseki": "Fuseki",
    "graphdb": "GraphDB",
    "owlready2": "Owlready2",
    "oxigraph": "Oxigraph",
    "qendpoint": "qEndpoint",
    "qlever": "QLever",
    "rdflib": "RDFLib",
    "rdflib-bdb": "RDFLib+BDB",
    "rdfox": "RDFox",
    "stardog": "Stardog",
    "virtuoso": "Virtuoso",
}

# Queries to gate at presentation time, rendered as `n.d.` on the datasets they are not in.
# Needed for a query that runs everywhere but returns an empty result on some ontology,
# whose timing would otherwise be compared against a real one.
# Empty: the two taxonomy queries used to sit here, but their empty results on Heritage and
# Urban came from a hardcoded root class name, not from a missing hierarchy — the root is
# parameterised per dataset in datasets.py now. Query.applies_to gates at the runner level.
APPLICABLE_ONLY: dict[str, set[str]] = {}

# Cell states.
OK = "ok"
TIMEOUT = "timeout"
ERROR = "error"
NOT_APPLICABLE = "not_applicable"
NOT_RUN = "not_run"

# Reasons a cell has no measurement at all.
EXCLUDED = "excluded"        # config [exclude] table
LOAD_FAILED = "load_failed"  # the dataset never made it into the engine
MISSING = "missing"          # simply not reached (interrupted run)

# Net times are drawn on a log axis, so a query as fast as the connection floor still needs
# a positive value. 1 ms is about the resolution of the whole setup (HTTP round trip over
# loopback), so anything below it is reported as "at the floor" rather than as a number.
NET_FLOOR_S = 1e-3


@dataclass
class Cell:
    """One (engine, dataset, query) cell of the result grid."""

    engine: str
    dataset: str
    query: str
    state: str
    reason: str | None = None      # only for NOT_RUN
    runs: int = 0
    median_s: float | None = None
    net_s: float | None = None     # median minus this engine's base latency
    mean_s: float | None = None
    stddev_s: float | None = None
    min_s: float | None = None
    max_s: float | None = None
    rows: int | None = None
    peak_rss_bytes: int | None = None

    @property
    def cv(self) -> float | None:
        """Coefficient of variation: how noisy the repetitions were, relative to the mean."""
        if self.mean_s is None or self.stddev_s is None or self.mean_s == 0:
            return None
        return self.stddev_s / self.mean_s

    @property
    def plottable(self) -> bool:
        return self.state == OK and self.net_s is not None


@dataclass
class Load:
    """One (engine, dataset) load result."""

    engine: str
    dataset: str
    fmt: str | None
    ok: bool
    load_time_s: float | None
    disk_bytes: int | None
    peak_rss_bytes: int | None
    excluded: bool = False


class ResultSet:
    """The complete grid: every engine x dataset x query, present or explained."""

    def __init__(self, run_dir: str | Path):
        self.run_dir = Path(run_dir)
        self.config = BenchConfig.load_snapshot(self.run_dir / "config.json")
        self.timeout_s = self.config.timeout_s

        store = BenchStore(self.run_dir)
        try:
            summary = summarize(store)
            loads = load_rows(store)
        finally:
            store.close()

        self.engines = _ordered(
            {r["engine"] for r in summary} | set(self.config.engines),
            list(ENGINE_LABEL),
        )
        self.datasets = _ordered(
            {r["dataset"] for r in summary} | set(self.config.datasets),
            DATASET_ORDER,
        )
        self.queries = _ordered(
            {r["query"] for r in summary if r["query"] != BASELINE},
            QUERY_ORDER,
        )

        self._loads = {
            (r["engine"], r["dataset"]): Load(
                engine=r["engine"],
                dataset=r["dataset"],
                fmt=r["format"],
                ok=r["status"] == "ok",
                load_time_s=r["load_time_s"],
                disk_bytes=r["disk_bytes"],
                peak_rss_bytes=r["peak_rss_bytes"],
                excluded=self.is_excluded(r["engine"], r["dataset"]),
            )
            for r in loads
        }
        self._summary = {(r["engine"], r["dataset"], r["query"]): r for r in summary}
        self._cells = {
            (e, d, q): self._build_cell(e, d, q)
            for e in self.engines
            for d in self.datasets
            for q in [*self.queries, BASELINE]
        }

    # ------------------------------------------------------------------ lookups
    def is_excluded(self, engine: str, dataset: str) -> bool:
        return dataset in self.config.exclude.get(engine, [])

    def applicable(self, query: str, dataset: str) -> bool:
        allowed = APPLICABLE_ONLY.get(query)
        return allowed is None or dataset in allowed

    def cell(self, engine: str, dataset: str, query: str) -> Cell:
        return self._cells[(engine, dataset, query)]

    def baseline_s(self, engine: str, dataset: str) -> float | None:
        row = self._summary.get((engine, dataset, BASELINE))
        return row["wall_median_s"] if row else None

    def load(self, engine: str, dataset: str) -> Load | None:
        return self._loads.get((engine, dataset))

    def all_loads(self) -> list[Load]:
        return list(self._loads.values())

    def datasets_for(self, query: str) -> list[str]:
        return [d for d in self.datasets if self.applicable(query, d)]

    def queries_for(self, dataset: str) -> list[str]:
        return [q for q in self.queries if self.applicable(q, dataset)]

    def has_data(self, engine: str) -> bool:
        return any(self.cell(engine, d, q).plottable
                   for d in self.datasets for q in self.queries)

    def peak_rss_bytes(self, engine: str, dataset: str) -> int | None:
        """Worst peak RSS seen across this engine's queries on this dataset."""
        peaks = [c.peak_rss_bytes for q in [*self.queries, BASELINE]
                 if (c := self.cell(engine, dataset, q)).peak_rss_bytes]
        return max(peaks) if peaks else None

    # ------------------------------------------------------------------ building
    def _build_cell(self, engine: str, dataset: str, query: str) -> Cell:
        if query != BASELINE and not self.applicable(query, dataset):
            return Cell(engine, dataset, query, NOT_APPLICABLE)

        row = self._summary.get((engine, dataset, query))
        if row is None:
            return Cell(engine, dataset, query, NOT_RUN, reason=self._why_missing(engine, dataset))
        if row["runs_ok"] == 0:
            state = TIMEOUT if row["runs_timeout"] else ERROR
            return Cell(engine, dataset, query, state, runs=0,
                        peak_rss_bytes=row["peak_rss_bytes"])

        median = row["wall_median_s"]
        base = self.baseline_s(engine, dataset) if query != BASELINE else None
        net = median if base is None else max(median - base, NET_FLOOR_S)
        return Cell(
            engine=engine, dataset=dataset, query=query,
            state=TIMEOUT if row["runs_timeout"] else OK,
            runs=row["runs_ok"],
            median_s=median,
            net_s=net,
            mean_s=row["wall_mean_s"],
            stddev_s=row["wall_stddev_s"],
            min_s=row["wall_min_s"],
            max_s=row["wall_max_s"],
            rows=row["result_rows"],
            peak_rss_bytes=row["peak_rss_bytes"],
        )

    def _why_missing(self, engine: str, dataset: str) -> str:
        if self.is_excluded(engine, dataset):
            return EXCLUDED
        load = self._loads.get((engine, dataset))
        if load is not None and not load.ok:
            return LOAD_FAILED
        return MISSING


def _ordered(present: set[str], preferred: list[str]) -> list[str]:
    """Preferred order first, then anything unexpected, alphabetically."""
    known = [name for name in preferred if name in present]
    return known + sorted(present - set(preferred))


def dump_json(rs: ResultSet, path: str | Path) -> Path:
    """The same grid as machine-readable JSON, next to the generated LaTeX."""
    payload = {
        "run_name": rs.config.run_name,
        "timeout_s": rs.timeout_s,
        "repetitions": rs.config.repetitions,
        "cells": [vars(rs.cell(e, d, q)) | {"cv": rs.cell(e, d, q).cv}
                  for e in rs.engines for d in rs.datasets
                  for q in [*rs.queries, BASELINE]],
        "loads": [vars(load) for load in rs.all_loads()],
    }
    out = Path(path)
    out.write_text(json.dumps(payload, indent=2))
    return out
