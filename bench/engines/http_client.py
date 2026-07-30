"""Unified SPARQL-over-HTTP client.

Every engine is queried over the SPARQL HTTP protocol; the *result serialization* is
per-engine (the format an engine emits fastest — see ``AbstractEngine.result_format``),
because that choice materially changes latency (JSON is slow on some engines). The client
always reads the whole body (so transfer is timed) and counts rows with an O(n),
format-appropriate method that costs the same for every engine, so measured differences
reflect the engine's serialization rather than our parser.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import requests

# result format key -> (Accept mime, parser kind)
FORMATS = {
    "json": ("application/sparql-results+json", "json"),
    "xml": ("application/sparql-results+xml", "xml"),
    "csv": ("text/csv", "csv"),
    "tsv": ("text/tab-separated-values", "tsv"),
}


class QueryError(Exception):
    pass


class QueryTimeout(QueryError):
    """The query exceeded the per-query budget. Terminal: never retried on resume."""


@dataclass
class QueryResult:
    wall_s: float           # full client round-trip: send + execute + receive + parse
    rows: int               # number of result rows
    bytes: int              # size of the response body
    server_s: float | None  # server-reported execution time, when available


def _parse_server_time(payload: dict) -> float | None:
    """Extract server-side execution time (seconds) where the engine reports it.

    QLever puts a ``time.total`` string like ``"123ms"`` in the JSON envelope.
    """
    t = payload.get("time")
    if isinstance(t, dict):
        raw = t.get("total") or t.get("computeResult")
        if isinstance(raw, str):
            raw = raw.strip().lower()
            try:
                if raw.endswith("ms"):
                    return float(raw[:-2]) / 1000.0
                if raw.endswith("s"):
                    return float(raw[:-1])
                return float(raw) / 1000.0
            except ValueError:
                return None
    return None


def _count_lines_minus_header(body: bytes) -> int:
    """Data-row count for CSV/TSV: non-empty lines minus the mandatory header row."""
    if not body:
        return 0
    lines = body.count(b"\n")
    if not body.endswith(b"\n"):
        lines += 1  # last line has no trailing newline
    return max(0, lines - 1)


class SparqlHttpClient:
    def __init__(self, endpoint: str, timeout: float = 300.0,
                 default_graph: str | None = None, result_format: str = "tsv"):
        self.endpoint = endpoint
        self.timeout = timeout
        # Sent as the SPARQL-protocol default-graph-uri param (used by Virtuoso, which
        # has no implicit default-graph union). None for stores that query the real
        # default graph directly.
        self.default_graph = default_graph
        self.accept, self.kind = FORMATS[result_format]
        self._session = requests.Session()

    def query(self, sparql: str) -> QueryResult:
        headers = {"Accept": self.accept, "Content-Type": "application/sparql-query"}
        params = {"default-graph-uri": self.default_graph} if self.default_graph else None
        start = time.perf_counter()
        try:
            resp = self._session.post(
                self.endpoint, data=sparql.encode("utf-8"), params=params,
                headers=headers, timeout=self.timeout,
            )
        except requests.Timeout as e:  # subclass of RequestException — must come first
            raise QueryTimeout(f"timed out after {self.timeout:g}s") from e
        except requests.RequestException as e:
            raise QueryError(f"request failed: {e}") from e

        if resp.status_code != 200:
            snippet = resp.text[:500]
            raise QueryError(f"HTTP {resp.status_code}: {snippet}")

        body = resp.content
        rows, server_s = self._count(body)
        wall = time.perf_counter() - start
        return QueryResult(wall_s=wall, rows=rows, bytes=len(body), server_s=server_s)

    def _count(self, body: bytes) -> tuple[int, float | None]:
        if self.kind in ("csv", "tsv"):
            return _count_lines_minus_header(body), None
        if self.kind == "xml":
            return body.count(b"<result>") + body.count(b"<result "), None
        # json
        import json
        try:
            payload = json.loads(body)
        except ValueError:
            return _count_lines_minus_header(body), None
        if "boolean" in payload:  # ASK
            return 1, _parse_server_time(payload)
        bindings = payload.get("results", {}).get("bindings", [])
        return len(bindings), _parse_server_time(payload)

    def close(self) -> None:
        self._session.close()
