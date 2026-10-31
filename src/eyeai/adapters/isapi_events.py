"""Hikvision ISAPI event adapter (alertStream).

Protocol (from Hikvision ISAPI documentation, NOT verified on hardware here):
  GET /ISAPI/Event/notification/alertStream  (HTTP digest auth)
  -> multipart/mixed stream; each part is application/xml with an
     <EventNotificationAlert> document carrying eventType, eventState,
     dateTime (ISO 8601 with offset), channelID and optional extras.
  Heartbeat parts (eventType 'heartbeat') are not emitted; a read timeout
  longer than the heartbeat interval triggers a reconnect.

Provenance: NATIVE (the camera ran the analytics itself).
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..schema import Emit, Event, Provenance
from ._multipart import MultipartSplitter, boundary_from_content_type
from .base import ThreadedAdapter, resolve_source_ts

# eventType -> (kind, default label, severity)
EVENT_MAP: dict[str, tuple[str, str | None, str]] = {
    "linedetection": ("line_cross", None, "warning"),
    "fielddetection": ("intrusion", None, "warning"),
    "regionEntrance": ("region_entry", None, "info"),
    "regionExiting": ("region_exit", None, "info"),
    "VMD": ("motion", None, "info"),
    "shelteralarm": ("tamper", None, "warning"),
    "videoloss": ("video_loss", None, "warning"),
    "ANPR": ("anpr", "vehicle", "info"),
    "vehicledetection": ("anpr", "vehicle", "info"),
    "facedetection": ("face", "person", "info"),
    "peopleDetection": ("object", "person", "info"),
    "alarmInput": ("io_input", None, "info"),
    "IO": ("io_input", None, "info"),
}
HEARTBEAT_TYPES = {"heartbeat", "videoloss_heartbeat"}


@dataclass
class IsapiNotification:
    event_type: str
    event_state: str
    date_time: str | None
    channel_id: str | None
    description: str | None
    plate: str | None = None
    io_id: str | None = None
    extra: dict[str, str] = field(default_factory=dict)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_notification_xml(body: bytes) -> IsapiNotification | None:
    """Parse one <EventNotificationAlert> document. Returns None if not parseable."""
    text = body.strip()
    if not text:
        return None
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    fields: dict[str, str] = {}
    plate: str | None = None
    for el in root.iter():
        name = _local(el.tag)
        value = (el.text or "").strip()
        if name == "licensePlate" and value:
            plate = value
        elif value and name not in fields:
            fields[name] = value
    event_type = fields.get("eventType")
    if not event_type:
        return None
    return IsapiNotification(
        event_type=event_type,
        event_state=fields.get("eventState", "active"),
        date_time=fields.get("dateTime"),
        channel_id=fields.get("channelID") or fields.get("dynChannelID"),
        description=fields.get("eventDescription"),
        plate=plate,
        io_id=fields.get("inputIOPortID") or fields.get("alarmInputID"),
        extra={k: v for k, v in fields.items() if k in {"ipAddress", "activePostCount"}},
    )


def notification_to_event(
    note: IsapiNotification, site_id: str, device_id: str
) -> Event | None:
    """Map a parsed notification to an Event. Heartbeats yield None."""
    if note.event_type in HEARTBEAT_TYPES:
        return None
    ts, ts_source = resolve_source_ts(note.date_time)
    kind, label, severity = EVENT_MAP.get(note.event_type, ("device_event", None, "info"))
    attributes: dict[str, Any] = {
        "source_event_type": note.event_type,
        "event_state": note.event_state,
        "ts_source": ts_source,
    }
    if note.channel_id:
        attributes["channel"] = note.channel_id
    if note.description:
        attributes["description"] = note.description
    if note.plate:
        attributes["plate"] = note.plate
    if note.io_id:
        attributes["io_id"] = note.io_id
    attributes.update(note.extra)
    if kind == "io_input":
        label = "alarm_input"
    return Event(
        site_id=site_id,
        device_id=device_id,
        ts_utc=ts,
        kind=kind,
        label=label,
        confidence=1.0,
        provenance=Provenance.NATIVE,
        severity=severity,
        attributes=attributes,
    )


class IsapiEventAdapter(ThreadedAdapter):
    """Streams Hikvision alertStream events and emits native Events."""

    def __init__(
        self,
        site_id: str,
        device_id: str,
        emit: Emit,
        *,
        host: str,
        username: str,
        password: str,
        port: int = 80,
        scheme: str = "http",
        base_url: str | None = None,
        heartbeat_timeout: float = 30.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(site_id, device_id, emit, **kwargs)
        self._base_url = (base_url or f"{scheme}://{host}:{port}").rstrip("/")
        self._username = username
        self._password = password
        self._heartbeat_timeout = heartbeat_timeout

    def _session(self) -> None:
        url = f"{self._base_url}/ISAPI/Event/notification/alertStream"
        timeout = httpx.Timeout(10.0, read=self._heartbeat_timeout)
        auth = httpx.DigestAuth(self._username, self._password)
        with httpx.Client(auth=auth, timeout=timeout) as client:
            with client.stream("GET", url) as resp:
                resp.raise_for_status()
                boundary = boundary_from_content_type(resp.headers.get("content-type", ""))
                if boundary is None:
                    raise ValueError("alertStream response has no multipart boundary")
                splitter = MultipartSplitter(boundary)
                self._set_connected(True)
                for chunk in resp.iter_bytes():
                    if self._stop_requested:
                        return
                    for part in splitter.feed(chunk):
                        self._handle_body(part.body)

    def _handle_body(self, body: bytes) -> None:
        note = parse_notification_xml(body)
        if note is None:
            return
        event = notification_to_event(note, self.site_id, self.device_id)
        if event is not None:
            self._emit_event(event)
