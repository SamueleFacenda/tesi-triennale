"""Resumable persistence for a benchmark run (SQLite in the run directory).

Layout of a run directory (``<output_dir>/<run_name>/``)::

    config.json                     frozen config snapshot
    bench.db                        this database (results + progress)
    storage/<engine>/<dataset>/     each engine's persisted DB for a dataset
    storage/<engine>/<engine>.log   server / loader logs

Every write commits immediately, so the process is safe to Ctrl-C and resume: the runner
skips any (engine, dataset, query, rep) that already has a successful measured row, and
skips loading when a successful ``load_result`` row exists and the store is intact. A
``status='timeout'`` row is terminal — that query is not retried on resume (see
:meth:`BenchStore.query_complete`); plain errors are cheap and still retried.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

# name used for the serialization/connection-overhead baseline query
BASELINE = "__baseline__"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS load_result (
    engine        TEXT NOT NULL,
    dataset       TEXT NOT NULL,
    format        TEXT,
    load_time_s   REAL,
    disk_bytes    INTEGER,
    peak_rss_bytes INTEGER,
    status        TEXT NOT NULL,   -- 'ok' | 'error'
    error         TEXT,
    ts            REAL NOT NULL,
    PRIMARY KEY (engine, dataset)
);

CREATE TABLE IF NOT EXISTS measurement (
    engine        TEXT NOT NULL,
    dataset       TEXT NOT NULL,
    query         TEXT NOT NULL,
    rep           INTEGER NOT NULL,
    warmup        INTEGER NOT NULL,   -- 1 for warmup runs (excluded from stats)
    wall_s        REAL,
    server_s      REAL,
    rows          INTEGER,
    bytes         INTEGER,
    peak_rss_bytes INTEGER,
    status        TEXT NOT NULL,      -- 'ok' | 'error' | 'timeout'
    error         TEXT,
    ts            REAL NOT NULL,
    PRIMARY KEY (engine, dataset, query, rep, warmup)
);
"""


class BenchStore:
    def __init__(self, run_dir: str | Path):
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.run_dir / "bench.db")
        self.db.row_factory = sqlite3.Row
        self.db.executescript(_SCHEMA)
        self.db.commit()

    # ------------------------------------------------------------------ storage dirs
    def storage_dir(self, engine: str, dataset: str) -> Path:
        p = self.run_dir / "storage" / engine / dataset
        p.mkdir(parents=True, exist_ok=True)
        return p

    # ------------------------------------------------------------------ loading
    def save_load(self, engine: str, dataset: str, *, fmt: str | None = None,
                  load_time_s: float | None, disk_bytes: int | None,
                  peak_rss_bytes: int | None, status: str, error: str | None = None) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO load_result "
            "(engine, dataset, format, load_time_s, disk_bytes, peak_rss_bytes, status, error, ts) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (engine, dataset, fmt, load_time_s, disk_bytes, peak_rss_bytes, status, error, time.time()),
        )
        self.db.commit()

    def load_ok(self, engine: str, dataset: str) -> bool:
        row = self.db.execute(
            "SELECT status FROM load_result WHERE engine=? AND dataset=?",
            (engine, dataset),
        ).fetchone()
        return row is not None and row["status"] == "ok"

    # ------------------------------------------------------------------ measurements
    def save_measurement(self, engine: str, dataset: str, query: str, rep: int, warmup: bool,
                         *, wall_s: float | None, server_s: float | None, rows: int | None,
                         bytes_: int | None, peak_rss_bytes: int | None,
                         status: str, error: str | None = None) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO measurement "
            "(engine, dataset, query, rep, warmup, wall_s, server_s, rows, bytes, "
            " peak_rss_bytes, status, error, ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (engine, dataset, query, rep, int(warmup), wall_s, server_s, rows, bytes_,
             peak_rss_bytes, status, error, time.time()),
        )
        self.db.commit()

    def measured_ok(self, engine: str, dataset: str, query: str, rep: int) -> bool:
        row = self.db.execute(
            "SELECT status FROM measurement WHERE engine=? AND dataset=? AND query=? "
            "AND rep=? AND warmup=0",
            (engine, dataset, query, rep),
        ).fetchone()
        return row is not None and row["status"] == "ok"

    def measured_count(self, engine: str, dataset: str, query: str) -> int:
        row = self.db.execute(
            "SELECT COUNT(*) AS n FROM measurement WHERE engine=? AND dataset=? AND query=? "
            "AND warmup=0 AND status='ok'",
            (engine, dataset, query),
        ).fetchone()
        return row["n"]

    def timed_out(self, engine: str, dataset: str, query: str) -> bool:
        """The query already blew the per-query budget here; running it again would too."""
        row = self.db.execute(
            "SELECT 1 FROM measurement WHERE engine=? AND dataset=? AND query=? "
            "AND status='timeout' LIMIT 1",
            (engine, dataset, query),
        ).fetchone()
        return row is not None

    def query_complete(self, engine: str, dataset: str, query: str, reps: int) -> bool:
        """Nothing left to run for this query: fully measured, or a recorded timeout."""
        return self.timed_out(engine, dataset, query) or \
            self.measured_count(engine, dataset, query) >= reps

    # ------------------------------------------------------------------ reporting
    def loads(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM load_result ORDER BY engine, dataset").fetchall()

    def measurements(self, *, measured_only: bool = True) -> list[sqlite3.Row]:
        q = "SELECT * FROM measurement"
        if measured_only:
            q += " WHERE warmup=0"
        q += " ORDER BY engine, dataset, query, rep"
        return self.db.execute(q).fetchall()

    def close(self) -> None:
        self.db.close()
