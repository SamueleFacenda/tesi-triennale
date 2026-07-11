"""kbench command-line interface.

    kbench run       --config bench.toml [--run-name N] [--no-tui] [--fresh]
    kbench resume    --run-dir runs/default [--no-tui]
    kbench status    --run-dir runs/default
    kbench report    --run-dir runs/default [--format csv|json|parquet] [--out DIR]
    kbench list-engines | list-queries | list-datasets
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from . import datasets as datasets_mod
from . import engines as engines_mod
from . import queries as queries_mod
from . import report as report_mod
from .config import BenchConfig
from .runner import BenchRunner
from .store import BenchStore

_SNAPSHOT = "config.json"


def _reporter(no_tui: bool):
    from .tui import PlainReporter, RichReporter
    if no_tui or not sys.stdout.isatty():
        return PlainReporter()
    return RichReporter()


def _run_dir(cfg: BenchConfig) -> Path:
    return Path(cfg.output_dir) / cfg.run_name


def _execute(cfg: BenchConfig, run_dir: Path, no_tui: bool) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    cfg.save_snapshot(run_dir / _SNAPSHOT)
    store = BenchStore(run_dir)
    reporter = _reporter(no_tui)
    try:
        BenchRunner(cfg, store, reporter).run()
    except KeyboardInterrupt:
        reporter.log("interrupted — progress saved; `kbench resume` to continue")
    finally:
        store.close()
    print(f"\nrun dir: {run_dir}")


def cmd_run(args: argparse.Namespace) -> int:
    cfg = BenchConfig.load(args.config)
    if args.run_name:
        cfg.run_name = args.run_name
    run_dir = _run_dir(cfg)
    if args.fresh and run_dir.exists():
        shutil.rmtree(run_dir)
    _execute(cfg, run_dir, args.no_tui)
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    snap = run_dir / _SNAPSHOT
    if not snap.is_file():
        print(f"no {_SNAPSHOT} in {run_dir}; nothing to resume", file=sys.stderr)
        return 1
    cfg = BenchConfig.load_snapshot(snap)
    _execute(cfg, run_dir, args.no_tui)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    store = BenchStore(args.run_dir)
    counts = report_mod.status_counts(store)
    print(f"run dir: {args.run_dir}")
    for k, v in counts.items():
        print(f"  {k:20} {v}")
    print("\nloads:")
    for r in store.loads():
        t = f"{r['load_time_s']:.1f}s" if r["load_time_s"] is not None else "-"
        print(f"  {r['engine']:12} {r['dataset']:18} {r['status']:6} {t}")
    store.close()
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    store = BenchStore(args.run_dir)
    out_dir = Path(args.out) if args.out else Path(args.run_dir) / "results"
    written = report_mod.export(store, args.format, out_dir)
    store.close()
    for p in written:
        print(p)
    return 0


def cmd_list_engines(args: argparse.Namespace) -> int:
    for name, (ok, reason) in engines_mod.available_engines().items():
        mark = "✓" if ok else "✗"
        print(f"  {mark} {name:12} {reason}")
    return 0


def cmd_list_queries(args: argparse.Namespace) -> int:
    for q in queries_mod.all_queries():
        print(f"  {q.name:24} [{q.kind}] {q.description}")
    return 0


def cmd_list_datasets(args: argparse.Namespace) -> int:
    for ds in datasets_mod.all_datasets():
        mark = "✓" if ds.available() else "✗"
        size = ds.size_bytes()
        human = f"{size / 1e9:.2f} GB" if size else "missing"
        print(f"  {mark} {ds.name:20} {ds.fmt:6} {human:>10}  {ds.namespace}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="kbench", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="run a benchmark from a config file")
    r.add_argument("--config", required=True)
    r.add_argument("--run-name", default=None)
    r.add_argument("--no-tui", action="store_true")
    r.add_argument("--fresh", action="store_true", help="wipe the run dir first")
    r.set_defaults(func=cmd_run)

    rs = sub.add_parser("resume", help="continue an interrupted run")
    rs.add_argument("--run-dir", required=True)
    rs.add_argument("--no-tui", action="store_true")
    rs.set_defaults(func=cmd_resume)

    st = sub.add_parser("status", help="show progress of a run")
    st.add_argument("--run-dir", required=True)
    st.set_defaults(func=cmd_status)

    rp = sub.add_parser("report", help="export aggregated results")
    rp.add_argument("--run-dir", required=True)
    rp.add_argument("--format", choices=["csv", "json", "parquet"], default="csv")
    rp.add_argument("--out", default=None)
    rp.set_defaults(func=cmd_report)

    sub.add_parser("list-engines", help="list engines and availability").set_defaults(func=cmd_list_engines)
    sub.add_parser("list-queries", help="list registered queries").set_defaults(func=cmd_list_queries)
    sub.add_parser("list-datasets", help="list datasets and availability").set_defaults(func=cmd_list_datasets)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
