from __future__ import annotations

from eyeai.adapters.isapi_events import (
    notification_to_event,
    parse_notification_xml,
)
from eyeai.schema import Provenance
from tests.adapters.mocks.camera_fixtures import isapi_event_xml

SITE = "site-1"
DEV = "cam-1"


def test_line_crossing_maps_to_native_event() -> None:
    note = parse_notification_xml(isapi_event_xml("linedetection", channel=3))
    assert note is not None
    ev = notification_to_event(note, SITE, DEV)
    assert ev is not None
    assert ev.provenance is Provenance.NATIVE
    assert ev.device_id == DEV
    assert ev.site_id == SITE
    assert ev.ts_utc.tzinfo is not None


def test_heartbeat_produces_no_event() -> None:
    note = parse_notification_xml(isapi_event_xml("heartbeat"))
    assert note is None or notification_to_event(note, SITE, DEV) is None


def test_inactive_state_recorded_not_dropped() -> None:
    note = parse_notification_xml(isapi_event_xml("VMD", state="inactive"))
    assert note is not None
    assert note.event_state == "inactive"


def test_unknown_event_type_maps_to_device_event() -> None:
    note = parse_notification_xml(isapi_event_xml("someVendorThing"))
    assert note is not None
    ev = notification_to_event(note, SITE, DEV)
    assert ev is not None
    assert ev.kind == "device_event"
    assert ev.provenance is Provenance.NATIVE


def test_malformed_xml_returns_none() -> None:
    assert parse_notification_xml(b"<EventNotificationAlert><unclosed>") is None


def test_doctype_is_rejected() -> None:
    body = (
        b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e "boom">]>'
        b"<EventNotificationAlert>&e;</EventNotificationAlert>"
    )
    assert parse_notification_xml(body) is None
