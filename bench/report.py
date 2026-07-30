"""Aggregate and export benchmark results to machine-readable formats."""

from __future__ import annotations

import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path

from .store import BASELINE, BenchStore


def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def _stats(values: list[float]) -> dict:
    clean = [v for v in values if v is not None]
    if not clean:
        return {"n": 0}
    return {
        "n": len(clean),
        "min": min(clean),
        "median": statistics.median(clean),
        "mean": statistics.fmean(clean),
        "p95": _pct(clean, 0.95),
        "max": max(clean),
        "stddev": statistics.pstdev(clean) if len(clean) > 1 else 0.0,
    }


def summarize(store: BenchStore) -> list[dict]:
    """One aggregated row per (engine, dataset, query) over the measured (non-warmup) runs."""
    grouped: dict[tuple, list] = defaultdict(list)
    for m in store.measurements(measured_only=True):
        grouped[(m["engine"], m["dataset"], m["query"])].append(m)

    rows = []
    for (engine, dataset, query), ms in sorted(grouped.items()):
        oks = [m for m in ms if m["status"] == "ok"]
        wall = _stats([m["wall_s"] for m in oks])
        server = _stats([m["server_s"] for m in oks if m["server_s"] is not None])
        rss = _stats([m["peak_rss_bytes"] for m in oks if m["peak_rss_bytes"]])
        result_rows = oks[0]["rows"] if oks else None
        rows.append({
            "engine": engine,
            "dataset": dataset,
            "query": query,
            "runs_ok": len(oks),
            "runs_err": sum(1 for m in ms if m["status"] == "error"),
            # >0 means the query exceeded timeout_s here; latencies below are then null
            "runs_timeout": sum(1 for m in ms if m["status"] == "timeout"),
            "result_rows": result_rows,
            "wall_min_s": wall.get("min"),
            "wall_median_s": wall.get("median"),
            "wall_mean_s": wall.get("mean"),
            "wall_p95_s": wall.get("p95"),
            "wall_stddev_s": wall.get("stddev"),
            "server_median_s": server.get("median"),
            # transport + (de)serialization estimate = wall - server
            "overhead_median_s": (
                wall["median"] - server["median"]
                if wall.get("median") is not None and server.get("median") is not None
                else None
            ),
            "peak_rss_bytes": rss.get("max"),
        })
    return rows


def load_rows(store: BenchStore) -> list[dict]:
    return [{
        "engine": r["engine"],
        "dataset": r["dataset"],
        "format": r["format"],
        "status": r["status"],
        "load_time_s": r["load_time_s"],
        "disk_bytes": r["disk_bytes"],
        "peak_rss_bytes": r["peak_rss_bytes"],
        "error": r["error"],
    } for r in store.loads()]


def raw_rows(store: BenchStore) -> list[dict]:
    return [dict(m) for m in store.measurements(measured_only=True)]


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("")
        return
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def export(store: BenchStore, fmt: str, out_dir: str | Path) -> list[Path]:
    """Write summary + loads + raw measurements. Returns the files written."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    summary = summarize(store)
    loads = load_rows(store)
    raw = raw_rows(store)
    written: list[Path] = []

    if fmt == "json":
        p = out / "results.json"
        p.write_text(json.dumps(
            {"summary": summary, "loads": loads, "measurements": raw},
            indent=2, default=str,
        ))
        written.append(p)
    elif fmt == "csv":
        for name, rows in [("summary", summary), ("loads", loads), ("measurements", raw)]:
            p = out / f"{name}.csv"
            _write_csv(p, rows)
            written.append(p)
    elif fmt == "parquet":
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError as e:
            raise RuntimeError("parquet export needs pyarrow (not installed)") from e
        for name, rows in [("summary", summary), ("loads", loads), ("measurements", raw)]:
            p = out / f"{name}.parquet"
            table = pa.Table.from_pylist(rows) if rows else pa.table({})
            pq.write_table(table, p)
            written.append(p)
    else:
        raise ValueError(f"unknown format {fmt!r} (use csv, json or parquet)")
    return written


def status_counts(store: BenchStore) -> dict:
    ms = store.measurements(measured_only=True)
    ok = sum(1 for m in ms if m["status"] == "ok" and m["query"] != BASELINE)
    err = sum(1 for m in ms if m["status"] == "error" and m["query"] != BASELINE)
    timeout = sum(1 for m in ms if m["status"] == "timeout" and m["query"] != BASELINE)
    loads = store.loads()
    return {
        "measurements_ok": ok,
        "measurements_err": err,
        "measurements_timeout": timeout,
        "loads_ok": sum(1 for r in loads if r["status"] == "ok"),
        "loads_err": sum(1 for r in loads if r["status"] == "error"),
    }
