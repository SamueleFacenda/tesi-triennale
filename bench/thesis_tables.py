"""Generate the LaTeX the thesis includes: figure floats and the appendix result tables.

The files are written into ``thesis/generated/`` and are meant to be overwritten by a new
``kbench thesis`` run, never edited by hand. Text here is Italian because it ends up in the
document; the code around it is not.
"""

from __future__ import annotations

from pathlib import Path

from .store import BASELINE
from .thesis_export import (
    DATASET_LABEL,
    ENGINE_LABEL,
    ERROR,
    EXCLUDED,
    LOAD_FAILED,
    MISSING,
    NOT_APPLICABLE,
    NOT_RUN,
    QUERY_LABEL,
    TIMEOUT,
    Cell,
    ResultSet,
)

_HEADER = ("% Generato da `kbench thesis`: non modificare a mano.\n"
           "% Rigenerare con: kbench thesis --run-dir runs/big\n\n")

_PLOTS = "images/plots"

_MARKER_INTRO = (
    "I tempi sono le mediane misurate meno la latenza base del motore, in scala "
    "logaritmica, e le barre verticali coprono l'intervallo fra minimo e massimo. "
    "Una barra appoggiata al limite inferiore dell'asse indica un tempo sotto il "
    "millisecondo, non distinguibile da tale latenza."
)

# Only the markers `thesis_charts._query_bar` can actually draw, in reading order.
_MARKER_LEGEND = (
    (TIMEOUT, "\\textsf{TO} segnala il superamento del timeout"),
    (NOT_APPLICABLE, "\\textsf{n.d.} una query non applicabile al dataset"),
    (NOT_RUN, "\\textsf{n.e.} una misurazione non eseguita"),
)

_REASON_NOTE = {
    EXCLUDED: "esclusione dalla configurazione del benchmark",
    LOAD_FAILED: "caricamento del dataset fallito",
    MISSING: "misurazioni non eseguite",
}


# ------------------------------------------------------------------ formatting
def _italian(text: str) -> str:
    """1,234.5 (C locale) -> 1.234,5 (Italian), as the rest of the thesis writes numbers."""
    return text.replace(",", "\u0000").replace(".", ",").replace("\u0000", ".")


def _seconds(value: float | None) -> str:
    if value is None:
        return "--"
    decimals = (0 if value >= 100 else 1 if value >= 10 else 2 if value >= 1
                else 3 if value >= 0.01 else 4)
    return _italian(f"{value:,.{decimals}f}")


def _percent(value: float | None) -> str:
    return "--" if value is None else _italian(f"{value * 100:,.1f}") + "\\%"


def _integer(value: int | None) -> str:
    return "--" if value is None else _italian(f"{value:,d}")


def _gigabytes(value: int | None) -> str:
    return "--" if value is None else _italian(f"{value / 1e9:,.2f}")


def _cell_row(cell: Cell) -> list[str]:
    """The seven numeric columns of one measurement row."""
    if cell.state == NOT_APPLICABLE:
        return ["\\textsf{n.d.}"] + ["--"] * 6
    if cell.state == NOT_RUN:
        return ["\\textsf{n.e.}"] + ["--"] * 6
    if cell.state == ERROR:
        return ["\\textsf{err}"] + ["--"] * 6
    if cell.state == TIMEOUT and cell.runs == 0:
        return ["\\textsf{TO}"] + ["--"] * 6
    marker = "\\textsf{TO}" if cell.state == TIMEOUT else str(cell.runs)
    return [
        marker,
        _seconds(cell.median_s),
        _seconds(cell.mean_s),
        _percent(cell.cv),
        _seconds(cell.min_s),
        _seconds(cell.max_s),
        _integer(cell.rows),
    ]


# ------------------------------------------------------------------ figures
def _subfigure(path: str, caption: str, label: str) -> str:
    return (
        "  \\begin{subfigure}[b]{\\textwidth}\n"
        "    \\centering\n"
        f"    \\includegraphics[width=\\textwidth]{{{path}}}\n"
        f"    \\caption{{{caption}}}\n"
        f"    \\label{{{label}}}\n"
        "  \\end{subfigure}\n"
    )


def _figure(panels: list[tuple[str, str, str]], caption: str, label: str) -> str:
    body = "  \\\\[1.5ex]\n".join(_subfigure(*p) for p in panels)
    return (
        "\\begin{figure}[htbp]\n  \\centering\n"
        f"{body}"
        f"  \\caption{{{caption}}}\n"
        f"  \\label{{{label}}}\n"
        "\\end{figure}\n\n"
    )


def _marker_note(rs: ResultSet) -> str:
    """The legend, restricted to the markers this run's figures really contain."""
    states = rs.figure_states()
    parts = [text for state, text in _MARKER_LEGEND if state in states]
    return _MARKER_INTRO if not parts else f"{_MARKER_INTRO} {', '.join(parts)}."


def _chunks(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def _figure_series(panels: list[tuple[str, str, str]], key: str, caption: str,
                   marker_note: str) -> list[str]:
    """Two panels per float; the legend note is spelled out once and referred to after."""
    chunks = _chunks(panels, 2)
    out = []
    for i, chunk in enumerate(chunks, start=1):
        counter = f" ({i} di {len(chunks)})" if len(chunks) > 1 else ""
        note = marker_note if i == 1 else f"Notazione come nella figura~\\ref{{fig:{key}_1}}."
        out.append(_figure(chunk, f"{caption}{counter}. {note}", f"fig:{key}_{i}"))
    return out


def figures_tex(rs: ResultSet) -> str:
    out = [_HEADER]
    marker_note = _marker_note(rs)

    query_panels = [
        (f"{_PLOTS}/q_{q}.png",
         f"Query \\emph{{{QUERY_LABEL.get(q, q)}}}.",
         f"fig:q_{q}")
        for q in rs.queries
    ]
    out += _figure_series(
        query_panels, "queries",
        "Confronto fra i motori per singola query, raggruppati per dataset",
        marker_note)

    engine_panels = [
        (f"{_PLOTS}/e_{e}.png",
         f"{ENGINE_LABEL.get(e, e)}.",
         f"fig:e_{e}")
        for e in rs.engines if rs.has_data(e)
    ]
    out += _figure_series(
        engine_panels, "engines",
        "Confronto fra le query per singolo motore, raggruppate per dataset",
        marker_note)

    out.append(_figure(
        [(f"{_PLOTS}/load_time.png", "Tempo di caricamento.", "fig:load_time"),
         (f"{_PLOTS}/disk_size.png", "Spazio occupato su disco.", "fig:disk_size")],
        "Tempo e spazio richiesti dal caricamento dei dataset in ogni motore, "
        "in scala logaritmica.",
        "fig:loads",
    ))
    out.append(_figure(
        [(f"{_PLOTS}/peak_rss.png",
          "Picco di memoria residente, massimo su tutte le query del dataset.",
          "fig:peak_rss")],
        "Memoria utilizzata dai motori durante l'esecuzione delle query.",
        "fig:memory",
    ))
    return "".join(out)


# ------------------------------------------------------------------ tables
_COLUMNS = ("Query & n & mediana & media & CV & min & max & righe \\\\\n"
            " & & [s] & [s] & & [s] & [s] & \\\\\n")


def _missing_note(rs: ResultSet, dataset: str) -> str:
    """Why some engines have no row at all on this dataset."""
    by_reason: dict[str, list[str]] = {}
    for engine in rs.engines:
        cells = [rs.cell(engine, dataset, q) for q in rs.queries]
        if all(c.state == NOT_RUN for c in cells) and cells:
            by_reason.setdefault(cells[0].reason or MISSING, []).append(
                ENGINE_LABEL.get(engine, engine))
    if not by_reason:
        return ""
    parts = [f"{', '.join(names)} ({_REASON_NOTE.get(reason, reason)})"
             for reason, names in by_reason.items()]
    return "Motori assenti dalla tabella: " + ", ".join(parts) + "."


def _dataset_table(rs: ResultSet, dataset: str) -> str:
    label = DATASET_LABEL.get(dataset, dataset)
    caption = (f"Risultati completi sul dataset {label}. "
               f"I tempi sono quelli misurati, latenza base compresa; questa è riportata "
               f"nell'ultima riga di ciascun motore. CV è il coefficiente di variazione, "
               f"cioè il rapporto fra deviazione standard e media delle "
               f"{rs.config.repetitions} ripetizioni.")
    out = [
        "\\begin{small}\n",
        "\\begin{longtable}{lrrrrrr r}\n",
        f"\\caption{{{caption}}}\\label{{tab:raw_{dataset}}}\\\\\n",
        "\\toprule\n", _COLUMNS, "\\midrule\n\\endfirsthead\n",
        f"\\multicolumn{{8}}{{l}}{{\\small\\itshape segue: dataset {label}}}\\\\\n",
        "\\toprule\n", _COLUMNS, "\\midrule\n\\endhead\n",
        "\\endfoot\n",
        "\\bottomrule\n\\endlastfoot\n",
    ]

    for engine in rs.engines:
        cells = [rs.cell(engine, dataset, q) for q in [*rs.queries, BASELINE]]
        if all(c.state == NOT_RUN for c in cells):
            continue
        out.append(f"\\multicolumn{{8}}{{l}}{{\\textbf{{{ENGINE_LABEL.get(engine, engine)}}}}}\\\\\n")
        for cell in cells:
            name = QUERY_LABEL.get(cell.query, cell.query)
            out.append(f"\\quad {name} & " + " & ".join(_cell_row(cell)) + " \\\\\n")
        out.append("\\addlinespace\n")

    note = _missing_note(rs, dataset)
    if note:
        out.append(f"\\multicolumn{{8}}{{p{{0.95\\linewidth}}}}{{\\footnotesize {note}}}\\\\\n")
    out.append("\\end{longtable}\n\\end{small}\n\n")
    return "".join(out)


def _matrix_table(rs: ResultSet, caption: str, label: str, unit: str, value_of) -> str:
    cols = "l" + "r" * len(rs.datasets)
    head = " & ".join(DATASET_LABEL.get(d, d) for d in rs.datasets)
    out = [
        "\\begin{table}[H]\n\\centering\n\\begin{small}\n",
        f"\\begin{{tabular}}{{{cols}}}\n\\toprule\n",
        f"Motore & {head} \\\\\n",
        f" & \\multicolumn{{{len(rs.datasets)}}}{{c}}{{{unit}}} \\\\\n\\midrule\n",
    ]
    for engine in rs.engines:
        values = [value_of(engine, d) for d in rs.datasets]
        if all(v == "--" for v in values):
            continue
        out.append(f"{ENGINE_LABEL.get(engine, engine)} & " + " & ".join(values) + " \\\\\n")
    out += ["\\bottomrule\n\\end{tabular}\n\\end{small}\n",
            f"\\caption{{{caption}}}\n\\label{{{label}}}\n\\end{{table}}\n\n"]
    return "".join(out)


def _load_value(rs: ResultSet, engine: str, dataset: str, field: str) -> str:
    load = rs.load(engine, dataset)
    if load is None:
        return "\\textsf{n.e.}" if not rs.is_excluded(engine, dataset) else "\\textsf{escl.}"
    if not load.ok:
        return "\\textsf{err}"
    value = getattr(load, field)
    return _seconds(value) if field == "load_time_s" else _gigabytes(value)


def tables_tex(rs: ResultSet) -> str:
    out = [_HEADER]
    out.append(_matrix_table(
        rs,
        "Tempo impiegato per il caricamento iniziale di ogni dataset. "
        "\\textsf{err} indica un caricamento fallito, \\textsf{escl.} una coppia "
        "motore/dataset esclusa dalla configurazione.",
        "tab:load_time", "tempo di caricamento [s]",
        lambda e, d: _load_value(rs, e, d, "load_time_s"),
    ))
    out.append(_matrix_table(
        rs,
        "Spazio occupato su disco dal grafo persistito da ogni motore.",
        "tab:disk_size", "spazio su disco [GB]",
        lambda e, d: _load_value(rs, e, d, "disk_bytes"),
    ))
    out.append(_matrix_table(
        rs,
        "Picco di memoria residente (RSS) raggiunto durante l'esecuzione, "
        "massimo fra tutte le query del dataset.",
        "tab:peak_rss", "picco RSS [GB]",
        lambda e, d: _gigabytes(rs.peak_rss_bytes(e, d)),
    ))
    for dataset in rs.datasets:
        out.append(_dataset_table(rs, dataset))
    return "".join(out)


def write_all(rs: ResultSet, out_dir: str | Path) -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name, text in [("results_figures.tex", figures_tex(rs)),
                       ("results_tables.tex", tables_tex(rs))]:
        path = out / name
        path.write_text(text)
        written.append(path)
    return written
