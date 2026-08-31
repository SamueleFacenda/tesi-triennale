# Thesis output

How `kbench thesis` turns a finished run into the charts and LaTeX that the thesis includes.

`kbench thesis --run-dir runs/big` regenerates everything the thesis shows, straight from
`bench.db`:

- `thesis/images/plots/*.png` — one chart per query (engines grouped by dataset), one per
  engine (queries grouped by dataset), plus load time / disk size / peak RSS. Query times
  are medians **net of that engine's `__baseline__`**, on a log axis.
- `thesis/generated/results_figures.tex` — the figure floats, two panels each, `\input` by
  `chapters/benchmark.tex`.
- `thesis/generated/results_tables.tex` — the appendix tables (raw medians, means, CV,
  min/max, row counts, plus the base-latency row), `\input` by `attachments/attachment.tex`.
- `thesis/generated/results.json` — the same grid, machine-readable.

Cells with no measurement are labelled rather than dropped: `TO` timed out, `n.d.` the
query does not apply to that dataset, `n.e.` was never executed (excluded pair, failed
load, or an interrupted run). `n.d.` is decided by the same `Query.applies_to` the runner
gates on, so a cell the run deliberately skipped is not reported as one it failed to
reach.

The figure captions spell out only the markers the run actually produced: the legend is
built from the states present in the grid, so a marker no reader will find is not
promised. With every dataset pinning a `points_object`, for instance, nothing is `n.d.`
and the clause disappears on its own — and comes back if a later run needs it.

Both directories must be **committed**: `nix build .#thesis` builds from the git tree, so
untracked charts are invisible to it. To regenerate while a run is still in progress, copy
`bench.db` and `config.json` elsewhere and point `--run-dir` at the copy — `BenchStore`
opens the database read-write.

---

[← back to the README](../README.md)
