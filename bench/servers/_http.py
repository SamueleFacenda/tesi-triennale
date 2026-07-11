"""Minimal single-threaded HTTP SPARQL endpoint for the embedded engines.

``serve(port, run_query)`` exposes ``POST /sparql`` (and ``/``). ``run_query`` gets the
SPARQL text and the ``Accept`` header and returns ``(body: bytes, content_type: str)``.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable

RunQuery = Callable[[str, str], "tuple[bytes, str]"]


def serve(port: int, run_query: RunQuery) -> None:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep stdout clean (loader log captures errors)
            pass

        def _reply(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._reply(200, b"ok", "text/plain")  # liveness

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            sparql = self.rfile.read(length).decode("utf-8")
            accept = self.headers.get("Accept", "")
            # health probe: base._wait_healthy posts `ASK {}` — answer without the backend
            if sparql.strip().upper().startswith("ASK"):
                self._reply(200, b'{"head":{},"boolean":true}',
                            "application/sparql-results+json")
                return
            try:
                body, ctype = run_query(sparql, accept)
            except Exception as e:  # noqa: BLE001
                self._reply(400, str(e).encode("utf-8", "replace"), "text/plain")
                return
            self._reply(200, body, ctype)

    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
