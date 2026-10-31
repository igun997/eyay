from __future__ import annotations

import pytest

from eyeai.adapters.onvif_events import (
    _seconds,
    message_to_event,
    parse_pull_messages,
    parse_xml,
    wsse_header,
)
from eyeai.schema import Provenance
from tests.adapters.mocks.camera_fixtures import onvif_pull_response

SITE = "site-1"
DEV = "onvif-1"


def _msg(topic: str, value: str = "true", utc: str = "2026-01-15T03:00:00Z") -> dict:
    return {
        "topic": topic,
        "utc": utc,
        "operation": "Changed",
        "source": {"VideoSourceToken": "VS1"},
        "data": {"State": value},
    }


def test_pull_messages_are_parsed() -> None:
    xml = onvif_pull_response([_msg("tns1:RuleEngine/CellMotionDetector/Motion")])
    msgs = parse_pull_messages(xml.encode())
    assert len(msgs) == 1
    assert msgs[0]["topic"].endswith("CellMotionDetector/Motion")
    assert msgs[0]["data"]["State"] == "true"


def test_motion_true_maps_to_native_event() -> None:
    ev = message_to_event(_msg("tns1:RuleEngine/CellMotionDetector/Motion"), SITE, DEV)
    assert ev is not None
    assert ev.provenance is Provenance.NATIVE
    assert ev.ts_utc.tzinfo is not None


def test_naive_utc_is_not_trusted_as_source_time() -> None:
    ev = message_to_event(
        _msg("tns1:RuleEngine/CellMotionDetector/Motion", utc="2026-01-15T03:00:00"),
        SITE,
        DEV,
    )
    assert ev is not None
    assert ev.attributes["ts_source"] != "source"


def test_doctype_rejected() -> None:
    body = b'<!DOCTYPE x [<!ENTITY e "boom">]><a>&e;</a>'
    with pytest.raises(ValueError, match="DOCTYPE/ENTITY rejected"):
        parse_xml(body)


def test_wsse_header_contains_username_and_digest() -> None:
    header = wsse_header("admin", "secret")
    assert "admin" in header
    assert "PasswordDigest" in header


def test_seconds_accepts_seconds_and_minutes() -> None:
    assert _seconds("PT60S") == 60
    assert _seconds("PT2M") == 120


def test_seconds_rejects_other_forms() -> None:
    with pytest.raises(ValueError):
        _seconds("1 minute")
