from __future__ import annotations

from eyeai.adapters._multipart import MultipartSplitter
from eyeai.adapters.dahua_events import dahua_to_event, parse_dahua_part
from eyeai.schema import Provenance
from tests.adapters.mocks.camera_fixtures import dahua_part, dahua_stream

SITE = "site-1"
DEV = "nvr-1"


def _bodies(stream: bytes) -> list[bytes]:
    return [p.body for p in MultipartSplitter(b"boundary").feed(stream)]


def test_parse_part_fields() -> None:
    body = _bodies(dahua_stream(dahua_part("VideoMotion", "Start", 0, {"UTC": 1736905200})))[0]
    ev = parse_dahua_part(body)
    assert ev is not None
    assert ev.code == "VideoMotion"
    assert ev.action == "Start"
    assert ev.index == 0


def test_motion_start_maps_to_native_event() -> None:
    body = _bodies(dahua_stream(dahua_part("VideoMotion", "Start", 0, {"UTC": 1736905200})))[0]
    ev = dahua_to_event(parse_dahua_part(body), SITE, DEV)
    assert ev is not None
    assert ev.provenance is Provenance.NATIVE
    assert ev.ts_utc.tzinfo is not None


def test_utc_epoch_sets_source_timestamp() -> None:
    body = _bodies(dahua_stream(dahua_part("VideoMotion", "Start", 0, {"UTC": 1736905200})))[0]
    ev = dahua_to_event(parse_dahua_part(body), SITE, DEV)
    assert ev is not None
    assert ev.attributes["ts_source"] == "source"


def test_missing_utc_falls_back_to_arrival() -> None:
    body = _bodies(dahua_stream(dahua_part("VideoMotion", "Start", 0, {})))[0]
    ev = dahua_to_event(parse_dahua_part(body), SITE, DEV)
    assert ev is not None
    assert ev.attributes["ts_source"] == "arrival"


def test_unknown_code_is_dropped() -> None:
    body = _bodies(dahua_stream(dahua_part("SomethingUnmapped", "Start", 0, {})))[0]
    assert dahua_to_event(parse_dahua_part(body), SITE, DEV) is None


def test_non_event_body_is_ignored() -> None:
    assert parse_dahua_part(b"Heartbeat\r\n") is None
