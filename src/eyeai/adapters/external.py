"""External business records (WMS / ANPR / POS / access / maintenance / barcode).

Records arrive as JSON over HTTP POST to ``/events`` on a small stdlib server.
Every request must carry ``X-Signature: sha256=<hex>`` (or bare ``<hex>``), the
HMAC-SHA256 of the raw request body under the shared secret. Missing or wrong
signatures get HTTP 401 and nothing is emitted.

Record shape::

    {"type": "wms_order", "external_id": "SO-1001",
     "ts": "2026-01-05T09:12:00+07:00",   # optional; absent -> arrival time
     "zone": "dock-2", "label": "order released",   # optional
     "data": {...}}                       # optional JSON object

Emitted Event: ``provenance=EXTERNAL``, ``kind=<type>``, ``device_id=<source id>``,
``attributes`` = ``external_id``, ``ts_source``, ``data``.

Responses: 202 accepted, 400 not JSON, 401 bad signature, 404 unknown path,
405 wrong method, 411 length required, 413 body too large, 422 record invalid,
503 consumer failed (nothing recorded; the sender should retry).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from ..schema import Emit, Event, Provenance
from .base import resolve_source_ts

log = logging.getLogger("eyeai.adapters.external")

RECORD_TYPES = frozenset({"wms_order", "anpr", "pos_transaction", "access_log", "maintenance_log", "barcode_scan"})
DEFAULT_MAX_BODY = 64 * 1024


class RecordError(ValueError):
    """Raised when a record fails validation. The message is safe to return to the caller."""


def record_to_event(record: Any, *, site_id: str, source_id: str) -> Event:
    """Validate one business record and build its EXTERNAL Event."""
    if not isinstance(record, dict):
        raise RecordError("record must be a JSON object")
    rtype = record.get("type")
    if rtype not in RECORD_TYPES:
        raise RecordError(f"type must be one of {sorted(RECORD_TYPES)}")
    external_id = record.get("external_id")
    if not isinstance(external_id, str) or not external_id.strip():
        raise RecordError("external_id must be a non-empty string")
    raw_ts = record.get("ts")
    if raw_ts is not None and not isinstance(raw_ts, str):
        raise RecordError("ts must be an ISO 8601 string when present")
    for field in ("zone", "label"):
        value = record.get(field)
        if value is not None and not isinstance(value, str):
            raise RecordError(f"{field} must be a string when present")
    data = record.get("data", {})
    if not isinstance(data, dict):
        raise RecordError("data must be a JSON object when present")

    ts: datetime
    ts_source: str
    if raw_ts is None:
        ts, ts_source = resolve_source_ts(None)
    else:
        ts, ts_source = resolve_source_ts(raw_ts)

    return Event(
        site_id=site_id,
        device_id=source_id,
        ts_utc=ts,
        kind=rtype,
        label=record.get("label") or rtype,
        confidence=1.0,
        provenance=Provenance.EXTERNAL,
        zone=record.get("zone"),
        attributes={"external_id": external_id, "ts_source": ts_source, "data": data},
    )


def sign(body: bytes, secret: str) -> str:
    """Return the ``sha256=<hex>`` signature a sender must put in X-Signature."""
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _signature_ok(body: bytes, header: str | None, secret: str) -> bool:
    if not header:
        return False
    supplied = header.strip()
    if supplied.lower().startswith("sha256="):
        supplied = supplied[7:]
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, supplied.lower())


class WebhookServer:
    """Signed HTTP ingest for business records. Runs its server in a daemon thread.

    ``port=0`` binds an ephemeral port; read it back from ``bound_port`` after ``start()``.
    """

    def __init__(
        self,
        site_id: str,
        emit: Emit,
        *,
        secret: str,
        source_id: str = "external",
        host: str = "127.0.0.1",
        port: int = 8089,
        max_body: int = DEFAULT_MAX_BODY,
    ) -> None:
        if not secret:
            raise ValueError("a non-empty secret is required")
        if max_body <= 0:
            raise ValueError("max_body must be positive")
        self.site_id = site_id
        self.source_id = source_id
        self._emit = emit
        self._secret = secret
        self._host = host
        self._port = port
        self._max_body = max_body
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._last_event_ts: datetime | None = None
        self._last_error: str | None = None

    @property
    def bound_port(self) -> int | None:
        return self._server.server_address[1] if self._server else None

    def start(self) -> None:
        if self._server is not None:
            return
        owner = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "eyeai-external/1"

            def log_message(self, fmt: str, *args: Any) -> None:  # silence default stderr logging
                log.debug("webhook %s", fmt % args)

            def _reply(self, code: int, text: str) -> None:
                body = text.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802 - stdlib naming
                self._reply(405, "method not allowed")

            def do_PUT(self) -> None:  # noqa: N802
                self._reply(405, "method not allowed")

            def do_DELETE(self) -> None:  # noqa: N802
                self._reply(405, "method not allowed")

            def do_POST(self) -> None:  # noqa: N802
                if self.path.split("?", 1)[0] != "/events":
                    self._reply(404, "not found")
                    return
                try:
                    length = int(self.headers.get("Content-Length", "-1"))
                except ValueError:
                    self._reply(400, "bad content length")
                    return
                if length < 0:
                    self._reply(411, "content length required")
                    return
                if length > owner._max_body:
                    self._reply(413, "body too large")
                    return
                body = self.rfile.read(length)
                if not _signature_ok(body, self.headers.get("X-Signature"), owner._secret):
                    owner._set_error("rejected request: bad signature")
                    self._reply(401, "bad signature")
                    return
                try:
                    record = json.loads(body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self._reply(400, "body is not JSON")
                    return
                try:
                    event = record_to_event(record, site_id=owner.site_id, source_id=owner.source_id)
                except RecordError as exc:
                    self._reply(422, str(exc))
                    return
                if not owner._deliver(event):
                    self._reply(503, "consumer unavailable")
                    return
                self._reply(202, "accepted")

        self._server = ThreadingHTTPServer((self._host, self._port), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, name="eyeai-external", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        server, thread = self._server, self._thread
        self._server = None
        self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "last_event_ts": self._last_event_ts,
                "last_error": self._last_error,
                "connected": self._server is not None,
            }

    def _deliver(self, event: Event) -> bool:
        try:
            self._emit(event)
        except Exception as exc:  # noqa: BLE001 - a failing consumer must not kill the server
            self._set_error(f"emit failed: {type(exc).__name__}: {exc}")
            return False
        with self._lock:
            self._last_event_ts = event.ts_utc
        return True

    def _set_error(self, message: str) -> None:
        with self._lock:
            self._last_error = message


__all__ = ["RECORD_TYPES", "RecordError", "WebhookServer", "record_to_event", "sign"]
