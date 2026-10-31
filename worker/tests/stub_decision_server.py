"""Tiny System One (`/v1/systemone`) stub for tests: canned answers, records every request body."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

GOOD = {
    "model": "d1",
    "answers": {"class": {"type": "choice", "choice": "person",
                          "probabilities": {"person": 0.91, "vehicle": 0.02, "false_alarm": 0.07}, "confidence": 0.83}},
    "usage": {"input_tokens": 310, "output_tokens": 0},
}


class StubServer:
    def __init__(self, status: int = 200, body=None, raw: bytes | None = None):
        self.status, self.body, self.raw = status, GOOD if body is None else body, raw
        self.requests: list[dict] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("Content-Length", 0))
                outer.requests.append({"path": self.path, "body": json.loads(self.rfile.read(n))})
                data = outer.raw if outer.raw is not None else json.dumps(outer.body).encode()
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):  # silence
                pass

        self._srv = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self._srv.server_port}"
        self._t = threading.Thread(target=self._srv.serve_forever, daemon=True)

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *a):
        self._srv.shutdown()
        self._srv.server_close()
