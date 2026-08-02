"""Render the thesis charts as PNG images.

Every chart is the same grouped bar plot on a logarithmic time axis: groups on the x axis,
one coloured bar per series. Query times are drawn *net of the engine's base latency*
(the `__baseline__` query), so engines wrapped in a local HTTP server are not charged for
the wrapper; the raw numbers stay in the appendix tables.

Cells with no bar are as informative as the bars, so they are annotated in place:
``TO`` timed out, ``n.d.`` the query does not apply to that dataset, ``n.e.`` it was never
executed (engine excluded, load failed, or the run stopped first).
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

from .thesis_export import (  # noqa: E402
    DATASET_LABEL,
    ENGINE_LABEL,
    NET_FLOOR_S,
    NOT_APPLICABLE,
    NOT_RUN,
    QUERY_LABEL,
    TIMEOUT,
    Cell,
    ResultSet,
)

# Five hues from the validated categorical palette, reused twice: the second half of the
# engines repeats the hues with a hatch, so identity never rests on colour alone (and
# survives a greyscale print).
_HUES = ["#2a78d6", "#eb6834", "#1baf7a", "#4a3aa7", "#e34948"]
_HATCH = "//////"

# Eight distinct slots, one per query, in the palette's validated order.
_QUERY_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100",
                 "#e87ba4", "#008300", "#4a3aa7", "#e34948"]

_INK = "#0b0b0b"
_MUTED = "#52514e"
_GRID = "#d8d7d2"

_RC = {
    "figure.dpi": 200,
    "savefig.dpi": 200,
    "figure.facecolor": "white",
    "savefig.facecolor": "white",
    "axes.facecolor": "white",
    "font.family": "serif",
    "font.size": 9,
    "axes.labelsize": 9,
    "axes.titlesize": 10,
    "xtick.labelsize": 8.5,
    "ytick.labelsize": 8.5,
    "legend.fontsize": 8,
    "axes.edgecolor": _MUTED,
    "axes.labelcolor": _INK,
    "text.color": _INK,
    "xtick.color": _MUTED,
    "ytick.color": _MUTED,
    "axes.linewidth": 0.6,
    "grid.color": _GRID,
    "grid.linewidth": 0.5,
}


def _tint(color: str, amount: float = 0.75) -> tuple:
    """A washed-out version of a series colour — used to fill timed-out bars."""
    return tuple(c + (1 - c) * amount for c in matplotlib.colors.to_rgb(color))


def engine_style(engine: str, engines: list[str]) -> dict:
    i = engines.index(engine)
    return {"color": _HUES[i % len(_HUES)],
            "hatch": _HATCH if i >= len(_HUES) else None}


def query_style(query: str, queries: list[str]) -> dict:
    return {"color": _QUERY_COLORS[queries.index(query) % len(_QUERY_COLORS)], "hatch": None}


class _Bar:
    """One bar to draw, or the reason there is none."""

    def __init__(self, value=None, low=None, high=None, marker=None):
        self.value = value
        self.low = low
        self.high = high
        self.marker = marker


def _query_bar(cell: Cell, timeout_s: float) -> _Bar:
    if cell.state == NOT_APPLICABLE:
        return _Bar(marker="n.d.")
    if cell.state == NOT_RUN:
        return _Bar(marker="n.e.")
    if cell.state == TIMEOUT or cell.net_s is None:
        return _Bar(value=timeout_s, marker="TO")
    base = cell.median_s - cell.net_s
    low = max(cell.min_s - base, NET_FLOOR_S) if cell.min_s is not None else None
    high = max(cell.max_s - base, cell.net_s) if cell.max_s is not None else None
    return _Bar(value=cell.net_s, low=low, high=high)


def _draw(ax, group_labels: list[str], series: list[tuple[str, dict]],
          bars: dict[tuple[int, int], _Bar], *, ylabel: str,
          timeout_s: float | None = None) -> None:
    """Grouped bars on a log axis. `bars[(series_index, group_index)]` drives everything."""
    n_series = max(len(series), 1)
    slot = 0.82 / n_series
    values = [b.value for b in bars.values() if b.value]
    if not values:
        values = [1.0]
    floor = min(values) / 2.5
    ceiling = max(values) * 3

    for si, (label, style) in enumerate(series):
        offset = -0.41 + slot * (si + 0.5)
        for gi in range(len(group_labels)):
            bar = bars.get((si, gi), _Bar())
            x = gi + offset
            if bar.value is None:
                if bar.marker:
                    ax.text(x, floor * 1.35, bar.marker, rotation=90, ha="center",
                            va="bottom", fontsize=5.5, color=_MUTED)
                continue
            timed_out = bar.marker == "TO"
            ax.bar(x, bar.value - floor, bottom=floor, width=slot * 0.88,
                   color=_tint(style["color"]) if timed_out else style["color"],
                   hatch=_HATCH if timed_out else style["hatch"],
                   edgecolor=style["color"] if timed_out else "white",
                   linewidth=0.4, alpha=1.0)
            if timed_out:
                ax.text(x, bar.value * 1.1, "TO", rotation=90, ha="center", va="bottom",
                        fontsize=5.5, color=_MUTED)
            elif bar.low is not None and bar.high is not None and bar.high > bar.low:
                ax.plot([x, x], [bar.low, bar.high], color=_MUTED, linewidth=0.5,
                        solid_capstyle="butt", zorder=3)

    ax.set_yscale("log")
    ax.set_ylim(floor, ceiling)
    ax.set_xlim(-0.5, len(group_labels) - 0.5)
    ax.set_xticks(range(len(group_labels)), group_labels)
    ax.set_ylabel(ylabel)
    ax.yaxis.grid(True, which="major", zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    if timeout_s is not None and any(b.marker == "TO" for b in bars.values()):
        ax.axhline(timeout_s, color=_MUTED, linewidth=0.6, linestyle="--", zorder=1)


def _legend(fig, ax, series: list[tuple[str, dict]], extra_note: str | None = None) -> None:
    handles = [Patch(facecolor=s["color"], hatch=s["hatch"], edgecolor="white", label=name)
               for name, s in series]
    ncol = 5 if len(series) > 8 else 4
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.13),
              ncol=ncol, frameon=False, handlelength=1.2, handleheight=0.9,
              columnspacing=1.2, borderpad=0.2)
    if extra_note:
        fig.text(0.99, 0.01, extra_note, ha="right", va="bottom", fontsize=6, color=_MUTED)


def _save(fig, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, bbox_inches="tight", pad_inches=0.02, transparent=False)
    plt.close(fig)
    return out


# ------------------------------------------------------------------ the charts
def query_chart(rs: ResultSet, query: str, out_dir: Path) -> Path:
    """One query: every engine, on every dataset it applies to."""
    groups = rs.datasets_for(query)
    series = [(ENGINE_LABEL.get(e, e), engine_style(e, rs.engines))
              for e in rs.engines if rs.has_data(e)]
    engines = [e for e in rs.engines if rs.has_data(e)]
    bars = {(si, gi): _query_bar(rs.cell(e, d, query), rs.timeout_s)
            for si, e in enumerate(engines) for gi, d in enumerate(groups)}

    fig, ax = plt.subplots(figsize=(7.0, 2.9))
    _draw(ax, [DATASET_LABEL.get(d, d) for d in groups], series, bars,
          ylabel="tempo netto [s]", timeout_s=rs.timeout_s)
    _legend(fig, ax, series)
    return _save(fig, out_dir / f"q_{query}.png")


def engine_chart(rs: ResultSet, engine: str, out_dir: Path) -> Path:
    """One engine: every query, on every dataset."""
    groups = rs.datasets
    series = [(QUERY_LABEL.get(q, q), query_style(q, rs.queries)) for q in rs.queries]
    bars = {(si, gi): _query_bar(rs.cell(engine, d, q), rs.timeout_s)
            for si, q in enumerate(rs.queries) for gi, d in enumerate(groups)}

    fig, ax = plt.subplots(figsize=(7.0, 2.9))
    _draw(ax, [DATASET_LABEL.get(d, d) for d in groups], series, bars,
          ylabel="tempo netto [s]", timeout_s=rs.timeout_s)
    _legend(fig, ax, series)
    return _save(fig, out_dir / f"e_{engine}.png")


def _resource_chart(rs: ResultSet, out: Path, ylabel: str, value_of) -> Path:
    engines = [e for e in rs.engines if rs.has_data(e)]
    series = [(ENGINE_LABEL.get(e, e), engine_style(e, rs.engines)) for e in engines]
    bars = {}
    for si, engine in enumerate(engines):
        for gi, dataset in enumerate(rs.datasets):
            value = value_of(engine, dataset)
            bars[(si, gi)] = _Bar(value=value) if value else _Bar(marker="n.e.")

    fig, ax = plt.subplots(figsize=(7.0, 2.9))
    _draw(ax, [DATASET_LABEL.get(d, d) for d in rs.datasets], series, bars, ylabel=ylabel)
    _legend(fig, ax, series)
    return _save(fig, out)


def load_time_chart(rs: ResultSet, out_dir: Path) -> Path:
    def value(engine, dataset):
        load = rs.load(engine, dataset)
        return load.load_time_s if load and load.ok else None
    return _resource_chart(rs, out_dir / "load_time.png", "tempo di caricamento [s]", value)


def disk_chart(rs: ResultSet, out_dir: Path) -> Path:
    def value(engine, dataset):
        load = rs.load(engine, dataset)
        return load.disk_bytes / 1e9 if load and load.ok and load.disk_bytes else None
    return _resource_chart(rs, out_dir / "disk_size.png", "spazio su disco [GB]", value)


def rss_chart(rs: ResultSet, out_dir: Path) -> Path:
    def value(engine, dataset):
        peak = rs.peak_rss_bytes(engine, dataset)
        return peak / 1e9 if peak else None
    return _resource_chart(rs, out_dir / "peak_rss.png", "picco RSS [GB]", value)


def render_all(rs: ResultSet, out_dir: str | Path) -> list[Path]:
    out = Path(out_dir)
    with plt.rc_context(_RC):
        written = [query_chart(rs, q, out) for q in rs.queries]
        written += [engine_chart(rs, e, out) for e in rs.engines if rs.has_data(e)]
        written += [load_time_chart(rs, out), disk_chart(rs, out), rss_chart(rs, out)]
    return written
