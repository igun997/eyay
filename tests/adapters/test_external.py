from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import re
import socket
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

import pytest

from eyeai.adapters.external import RECORD_TYPES, RecordError, WebhookServer, record_to_event, sign
from eyeai.schema import Event, Provenance

SITE = "site-1"
SOURCE = "wms"
SECRET = "s3cret"


def _record(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "type": "anpr",
        "external_id": "ext-1",
        "ts": "2024-05-01T10:00:00+07:00",
        "zone": "gate",
        "label": "plate",
        "data": {"plate": "B1234XYZ"},
    }
    base.update(overrides)
    return base


def _body(record: Any) -> bytes:
    return json.dumps(record).encode("utf-8")


def _post(srv: WebhookServer, body: bytes, signature: str | None, path: str = "/events") -> int:
    headers = {"Content-Type": "application/json"}
    if signature is not None:
        headers["X-Signature"] = signature
    conn = http.client.HTTPConnection("127.0.0.1", srv.bound_port, timeout=5)
    try:
        conn.request("POST", path, body=body, headers=headers)
        resp = conn.getresponse()
        resp.read()
        return resp.status
    finally:
        conn.close()


def _raw_status(srv: WebhookServer, request: bytes) -> int:
    with socket.create_connection(("127.0.0.1", srv.bound_port), timeout=5) as sock:
        sock.sendall(request)
        reply = sock.recv(4096)
    return int(reply.split(b" ", 2)[1])


@pytest.fixture
def received() -> list[Event]:
    return []


@pytest.fixture
def server(received: list[Event]) -> Iterator[WebhookServer]:
    srv = WebhookServer(SITE, received.append, secret=SECRET, source_id=SOURCE, port=0)
    srv.start()
    try:
        yield srv
    finally:
        srv.stop()


def test_record_types_cover_the_documented_set() -> None:
    assert RECORD_TYPES == {
        "wms_order",
        "anpr",
        "pos_transaction",
        "access_log",
        "maintenance_log",
        "barcode_scan",
    }


@pytest.mark.parametrize("rtype", sorted(RECORD_TYPES))
def test_record_to_event_accepts_every_type(rtype: str) -> None:
    event = record_to_event(_record(type=rtype), site_id=SITE, source_id=SOURCE)
    assert event.kind == rtype
    assert event.provenance is Provenance.EXTERNAL


def test_record_to_event_maps_fields() -> None:
    event = record_to_event(_record(), site_id=SITE, source_id=SOURCE)
    assert event.site_id == SITE
    assert event.device_id == SOURCE
    assert event.label == "plate"
    assert event.zone == "gate"
    assert event.confidence == 1.0
    assert event.attributes["external_id"] == "ext-1"
    assert event.attributes["data"] == {"plate": "B1234XYZ"}


def test_offset_timestamp_is_converted_to_utc() -> None:
    event = record_to_event(_record(), site_id=SITE, source_id=SOURCE)
    assert event.ts_utc == datetime(2024, 5, 1, 3, 0, tzinfo=timezone.utc)
    assert event.ts_utc.tzinfo is not None
    assert event.attributes["ts_source"] == "source"


def test_naive_timestamp_is_treated_as_utc() -> None:
    event = record_to_event(_record(ts="2024-05-01T10:00:00"), site_id=SITE, source_id=SOURCE)
    assert event.ts_utc == datetime(2024, 5, 1, 10, 0, tzinfo=timezone.utc)
    assert event.attributes["ts_source"] == "source_naive_utc"


def test_absent_timestamp_falls_back_to_arrival() -> None:
    record = _record()
    del record["ts"]
    event = record_to_event(record, site_id=SITE, source_id=SOURCE)
    assert event.ts_utc.tzinfo is not None
    assert event.attributes["ts_source"] == "arrival"


def test_label_defaults_to_type_and_data_defaults_to_empty() -> None:
    record = _record()
    del record["label"]
    del record["data"]
    event = record_to_event(record, site_id=SITE, source_id=SOURCE)
    assert event.label == "anpr"
    assert event.attributes["data"] == {}


@pytest.mark.parametrize(
    ("record", "fragment"),
    [
        ([1, 2], "record must be a JSON object"),
        (_record(type="bogus"), "type must be one of"),
        (_record(external_id=""), "external_id must be a non-empty string"),
        (_record(external_id="   "), "external_id must be a non-empty string"),
        (_record(ts=123), "ts must be an ISO 8601 string"),
        (_record(zone=5), "zone must be a string"),
        (_record(label=5), "label must be a string"),
        (_record(data=[1]), "data must be a JSON object"),
    ],
)
def test_record_to_event_rejects_invalid_records(record: Any, fragment: str) -> None:
    with pytest.raises(RecordError, match=re.escape(fragment)):
        record_to_event(record, site_id=SITE, source_id=SOURCE)


def test_record_error_is_a_value_error() -> None:
    assert issubclass(RecordError, ValueError)


def test_sign_returns_sha256_prefixed_hex() -> None:
    body = b'{"a": 1}'
    expected = "sha256=" + hmac.new(SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
    assert sign(body, SECRET) == expected


def test_constructor_requires_a_secret() -> None:
    with pytest.raises(ValueError, match="a non-empty secret is required"):
        WebhookServer(SITE, lambda event: None, secret="")


def test_constructor_rejects_nonpositive_max_body() -> None:
    with pytest.raises(ValueError, match="max_body must be positive"):
        WebhookServer(SITE, lambda event: None, secret=SECRET, max_body=0)


def test_bound_port_is_none_until_started_and_none_after_stop() -> None:
    srv = WebhookServer(SITE, lambda event: None, secret=SECRET, port=0)
    assert srv.bound_port is None
    srv.start()
    assert srv.bound_port is not None
    assert srv.status()["connected"] is True
    srv.stop()
    assert srv.bound_port is None
    assert srv.status()["connected"] is False


def test_valid_signed_record_is_accepted_and_emitted(server: WebhookServer, received: list[Event]) -> None:
    body = _body(_record())
    assert _post(server, body, sign(body, SECRET)) == 202
    assert len(received) == 1
    assert received[0].provenance is Provenance.EXTERNAL
    assert received[0].device_id == SOURCE
    status = server.status()
    assert status["last_event_ts"] == received[0].ts_utc
    assert status["last_error"] is None


def test_bare_and_uppercase_hex_signatures_are_accepted(server: WebhookServer, received: list[Event]) -> None:
    body = _body(_record())
    bare = sign(body, SECRET)[len("sha256="):]
    assert _post(server, body, bare) == 202
    upper = "sha256=" + bare.upper()
    assert _post(server, body, upper) == 202
    assert len(received) == 2


def test_bad_signature_is_rejected_and_recorded(server: WebhookServer, received: list[Event]) -> None:
    body = _body(_record())
    assert _post(server, body, sign(body, "wrong-secret")) == 401
    assert received == []
    assert server.status()["last_error"] == "rejected request: bad signature"


def test_missing_signature_is_rejected(server: WebhookServer, received: list[Event]) -> None:
    body = _body(_record())
    assert _post(server, body, None) == 401
    assert received == []


def test_signature_over_different_body_is_rejected(server: WebhookServer, received: list[Event]) -> None:
    body = _body(_record())
    tampered = _body(_record(external_id="ext-2"))
    assert _post(server, tampered, sign(body, SECRET)) == 401
    assert received == []


def test_non_json_body_is_400(server: WebhookServer, received: list[Event]) -> None:
    body = b"not json"
    assert _post(server, body, sign(body, SECRET)) == 400
    assert received == []


def test_invalid_record_is_422(server: WebhookServer, received: list[Event]) -> None:
    body = _body(_record(type="bogus"))
    assert _post(server, body, sign(body, SECRET)) == 422
    assert received == []


def test_unknown_path_is_404(server: WebhookServer, received: list[Event]) -> None:
    body = _body(_record())
    assert _post(server, body, sign(body, SECRET), path="/other") == 404
    assert received == []


@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE"])
def test_other_methods_are_405(server: WebhookServer, method: str) -> None:
    conn = http.client.HTTPConnection("127.0.0.1", server.bound_port, timeout=5)
    try:
        conn.request(method, "/events")
        resp = conn.getresponse()
        resp.read()
        assert resp.status == 405
    finally:
        conn.close()


def test_missing_content_length_is_411(server: WebhookServer) -> None:
    request = b"POST /events HTTP/1.0\r\nHost: localhost\r\n\r\n"
    assert _raw_status(server, request) == 411


def test_non_numeric_content_length_is_400(server: WebhookServer) -> None:
    request = b"POST /events HTTP/1.0\r\nHost: localhost\r\nContent-Length: abc\r\n\r\n"
    assert _raw_status(server, request) == 400


def test_oversized_body_is_413() -> None:
    received: list[Event] = []
    srv = WebhookServer(SITE, received.append, secret=SECRET, port=0, max_body=8)
    srv.start()
    try:
        body = _body(_record())
        assert len(body) > 8
        assert _post(srv, body, sign(body, SECRET)) == 413
        assert received == []
    finally:
        srv.stop()


def test_consumer_failure_is_503_and_recorded() -> None:
    def failing(event: Event) -> None:
        raise RuntimeError("db down")

    srv = WebhookServer(SITE, failing, secret=SECRET, port=0)
    srv.start()
    try:
        body = _body(_record())
        assert _post(srv, body, sign(body, SECRET)) == 503
        assert srv.status()["last_error"] == "emit failed: RuntimeError: db down"
        assert srv.status()["last_event_ts"] is None
    finally:
        srv.stop()
