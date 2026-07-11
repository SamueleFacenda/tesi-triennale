"""Unified SPARQL-over-HTTP client.

Every engine is queried the same way — a SPARQL protocol request over HTTP with a JSON
result format — so the client-side connection + parse cost is identical and comparable
across engines. This is what lets us reason about serialization overhead.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import requests

_JSON = "application/sparql-results+json"


class QueryError(Exception):
    pass


@dataclass
class QueryResult:
    wall_s: float           # full client round-trip: send + execute + receive + parse
    rows: int               # number of result rows (bindings), or 1 for ASK
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


class SparqlHttpClient:
    def __init__(self, endpoint: str, timeout: float = 300.0, default_graph: str | None = None):
        self.endpoint = endpoint
        self.timeout = timeout
        # Sent as the SPARQL-protocol default-graph-uri param (used by Virtuoso, which
        # has no implicit default-graph union). None for stores that query the real
        # default graph directly.
        self.default_graph = default_graph
        self._session = requests.Session()

    def query(self, sparql: str) -> QueryResult:
        headers = {"Accept": _JSON, "Content-Type": "application/sparql-query"}
        params = {"default-graph-uri": self.default_graph} if self.default_graph else None
        start = time.perf_counter()
        try:
            resp = self._session.post(
                self.endpoint, data=sparql.encode("utf-8"), params=params,
                headers=headers, timeout=self.timeout,
            )
        except requests.RequestException as e:
            raise QueryError(f"request failed: {e}") from e

        if resp.status_code != 200:
            snippet = resp.text[:500]
            raise QueryError(f"HTTP {resp.status_code}: {snippet}")

        body = resp.content
        # Parse just enough to count rows and read the server time.
        rows, server_s = self._count(resp, body)
        wall = time.perf_counter() - start
        return QueryResult(wall_s=wall, rows=rows, bytes=len(body), server_s=server_s)

    @staticmethod
    def _count(resp: requests.Response, body: bytes) -> tuple[int, float | None]:
        try:
            payload = resp.json()
        except ValueError:
            # Not JSON (some engines fall back to XML/CSV); count lines conservatively.
            return max(0, body.count(b"\n") - 1), None
        if "boolean" in payload:  # ASK
            return 1, _parse_server_time(payload)
        bindings = payload.get("results", {}).get("bindings", [])
        return len(bindings), _parse_server_time(payload)

    def close(self) -> None:
        self._session.close()
