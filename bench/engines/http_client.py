"""Unified SPARQL-over-HTTP client.

Every engine is queried over the SPARQL HTTP protocol; the *result serialization* is
per-engine (the format an engine emits fastest — see ``AbstractEngine.result_format``),
because that choice materially changes latency (JSON is slow on some engines). The client
always reads the whole body (so transfer is timed) and counts rows with an O(n),
format-appropriate method that costs the same for every engine, so measured differences
reflect the engine's serialization rather than our parser.

One engine (Virtuoso) truncates a single response at 2^20 rows, so the client can fetch a
result in ``OFFSET``/``LIMIT`` pages — see :meth:`SparqlHttpClient._query_chunked`.

``timeout`` is a wall-clock budget for the *whole* query, pages and body transfer included
— not the read-inactivity timeout ``requests`` would give us. See
:meth:`SparqlHttpClient._read_body`.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

import requests
from urllib3.exceptions import ReadTimeoutError

# result format key -> (Accept mime, parser kind)
FORMATS = {
    "json": ("application/sparql-results+json", "json"),
    "xml": ("application/sparql-results+xml", "xml"),
    "csv": ("text/csv", "csv"),
    "tsv": ("text/tab-separated-values", "tsv"),
}


# body is read in pieces so the deadline can be checked as it arrives
_READ_CHUNK = 1 << 16


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


_TRAILING_MODIFIER = re.compile(r"\b(LIMIT|OFFSET)\b", re.IGNORECASE)


def _has_trailing_modifier(sparql: str) -> bool:
    """Whether the query already ends with its own LIMIT/OFFSET.

    A solution modifier can only follow the query's last ``}``, so looking there is both
    sufficient and immune to a LIMIT nested in a subselect. Such a query cannot be paged —
    a second LIMIT is a syntax error — so it is sent as-is (``_SPB_Q2``, whose LIMIT 50 is
    part of the query).
    """
    tail = sparql[sparql.rfind("}") + 1:]
    return _TRAILING_MODIFIER.search(tail) is not None


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
                 default_graph: str | None = None, result_format: str = "tsv",
                 chunk_rows: int | None = None):
        self.endpoint = endpoint
        self.timeout = timeout
        # page size for endpoints that cap a single response (None = one request per
        # query, which is what every engine but Virtuoso does)
        self.chunk_rows = chunk_rows
        # Sent as the SPARQL-protocol default-graph-uri param (used by Virtuoso, which
        # has no implicit default-graph union). None for stores that query the real
        # default graph directly.
        self.default_graph = default_graph
        self.accept, self.kind = FORMATS[result_format]
        self._session = requests.Session()

    def query(self, sparql: str) -> QueryResult:
        if self.chunk_rows is None or _has_trailing_modifier(sparql):
            return self._request(sparql, self.timeout)
        return self._query_chunked(sparql)

    def _query_chunked(self, sparql: str) -> QueryResult:
        """Fetch the result one page at a time, stopping at the first short page.

        Virtuoso's endpoint truncates a single response at 2^20 rows and still answers
        200, so a large result has to be paged (same fix as the 3dont viewer). The pages
        are summed into one :class:`QueryResult`: that total is what a client actually
        pays to obtain the whole result, round-trips included. ``timeout`` is spent down
        across pages rather than granted per page, so a paged engine gets exactly the
        budget every other engine gets.
        """
        base = sparql.rstrip()
        offset = rows = nbytes = 0
        server_s, server_all = 0.0, True
        start = time.perf_counter()
        while True:
            left = self.timeout - (time.perf_counter() - start)
            paged = f"{base}\nOFFSET {offset} LIMIT {self.chunk_rows}\n"
            try:
                page = self._request(paged, left) if left > 0 else None
            except QueryTimeout:
                page = None
            if page is None:
                raise QueryTimeout(
                    f"timed out after {self.timeout:g}s "
                    f"({rows} rows in {offset // self.chunk_rows} chunks)"
                )
            rows += page.rows
            nbytes += page.bytes
            if page.server_s is None:
                server_all = False
            else:
                server_s += page.server_s
            if page.rows < self.chunk_rows:
                break
            offset += self.chunk_rows
        return QueryResult(
            wall_s=time.perf_counter() - start, rows=rows, bytes=nbytes,
            server_s=server_s if server_all else None,
        )

    def _request(self, sparql: str, timeout: float) -> QueryResult:
        headers = {"Accept": self.accept, "Content-Type": "application/sparql-query"}
        params = {"default-graph-uri": self.default_graph} if self.default_graph else None
        start = time.perf_counter()
        try:
            # streamed so the body can be read against a deadline; see _read_body
            resp = self._session.post(
                self.endpoint, data=sparql.encode("utf-8"), params=params,
                headers=headers, timeout=timeout, stream=True,
            )
        except requests.Timeout as e:  # subclass of RequestException — must come first
            raise QueryTimeout(f"timed out after {timeout:g}s") from e
        except requests.RequestException as e:
            raise QueryError(f"request failed: {e}") from e

        try:
            if resp.status_code != 200:
                # first chunk only: an error body is short, and reading it unbounded
                # would reintroduce the very stall the deadline is there to prevent
                snippet = next(resp.iter_content(500), b"").decode("utf-8", "replace")
                raise QueryError(f"HTTP {resp.status_code}: {snippet}")
            body = self._read_body(resp, start, timeout)
        finally:
            # release the pooled connection: a half-read streamed response would leave
            # the session handing the next query a dirty socket
            resp.close()

        rows, server_s = self._count(body)
        wall = time.perf_counter() - start
        return QueryResult(wall_s=wall, rows=rows, bytes=len(body), server_s=server_s)

    @staticmethod
    def _read_body(resp: requests.Response, start: float, timeout: float) -> bytes:
        """Read the whole body, giving up once ``timeout`` seconds have elapsed.

        ``requests``' own ``timeout`` bounds only the wait *between* two reads, so an
        engine that keeps trickling bytes never trips it — Oxigraph streams its results
        incrementally and once ran a single query for nine hours under a 600 s budget.
        Checking the deadline per chunk makes ``timeout`` the budget for the whole
        response: a query is not "in progress" for benchmark purposes just because rows
        are still dripping out.
        """
        chunks: list[bytes] = []
        nbytes = 0
        try:
            for chunk in resp.iter_content(_READ_CHUNK):
                chunks.append(chunk)
                nbytes += len(chunk)
                if time.perf_counter() - start > timeout:
                    raise QueryTimeout(
                        f"timed out after {timeout:g}s ({nbytes} bytes received)"
                    )
        except requests.RequestException as e:
            # a timeout hit while iterating a streamed body does not arrive as
            # requests.Timeout: iter_content re-raises urllib3's ReadTimeoutError as a
            # plain ConnectionError. Misclassifying it would make the runner retry a
            # query it should abandon, re-paying the budget on every rep and resume.
            if isinstance(e, requests.Timeout) or isinstance(
                    e.args[0] if e.args else None, ReadTimeoutError):
                raise QueryTimeout(
                    f"timed out after {timeout:g}s ({nbytes} bytes received)") from e
            raise QueryError(f"read failed after {nbytes} bytes: {e}") from e
        return b"".join(chunks)

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
