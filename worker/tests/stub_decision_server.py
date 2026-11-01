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
    """`noul`: P(yes) per rule key (default 0.05). `crash_multi`: HTTP 500 when a request has 2+ questions (what Ollama d1
    on CPU does). `fail_keys`: HTTP 500 when the request contains one of these question keys."""

    def __init__(self, status: int = 200, body=None, raw: bytes | None = None, noul: dict | None = None,
                 crash_multi: bool = False, fail_keys: set | None = None, delay_s: float = 0.0):
        self.status, self.body, self.raw = status, GOOD if body is None else body, raw
        self.noul, self.crash_multi, self.fail_keys, self.delay_s = noul or {}, crash_multi, fail_keys or set(), delay_s
        self.requests: list[dict] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n))
                outer.requests.append({"path": self.path, "body": body})
                qs = body.get("questions", {})
                status, payload = outer.status, outer.body
                if outer.delay_s:
                    import time as _t
                    _t.sleep(outer.delay_s)
                if outer.raw is None and status == 200:
                    if (outer.crash_multi and len(qs) > 1) or (outer.fail_keys & set(qs)):
                        status, payload = 500, {"error": "llama-server process has terminated"}
                    elif any(q.get("type") == "noul" for q in qs.values()):
                        def p(k):
                            v = outer.noul.get(k, 0.05)
                            return (v.pop(0) if len(v) > 1 else v[0]) if isinstance(v, list) else v

                        answers = {k: {"type": "noul", "noul": p(k)} for k, q in qs.items() if q.get("type") == "noul"}
                        if "class" in qs:
                            answers["class"] = GOOD["answers"]["class"]
                        payload = {"model": "d1", "answers": answers, "usage": {"input_tokens": 100, "output_tokens": 0}}
                data = outer.raw if outer.raw is not None else json.dumps(payload).encode()
                self.send_response(status)
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
