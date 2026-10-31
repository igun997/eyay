"""Dahua CGI event adapter (eventManager.cgi attach).

Protocol (from Dahua CGI documentation, NOT verified on hardware here):
  GET /cgi-bin/eventManager.cgi?action=attach&codes=[All]   (HTTP digest auth)
  -> multipart/x-mixed-replace stream. Each part is text of the form
       Code=<EventCode>;action=<Start|Stop|Pulse>;index=<n>
     optionally followed by `data={ ... }` JSON. Smart-event data carries
     "UTC" (epoch seconds) which is used as the source time when present.

Provenance: NATIVE (the camera/NVR ran the analytics itself).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from ..schema import Emit, Event, Provenance, utcnow
from ._multipart import MultipartSplitter, boundary_from_content_type
from .base import ThreadedAdapter

# Dahua event code -> (kind, label, severity)
CODE_MAP: dict[str, tuple[str, str | None, str]] = {
    "CrossLineDetection": ("line_cross", None, "warning"),
    "CrossRegionDetection": ("intrusion", None, "warning"),
    "VideoMotion": ("motion", None, "info"),
    "SmartMotionHuman": ("object", "person", "info"),
    "SmartMotionVehicle": ("object", "vehicle", "info"),
    "AlarmLocal": ("io_input", "alarm_input", "info"),
    "VideoBlind": ("tamper", None, "warning"),
    "VideoLoss": ("video_loss", None, "warning"),
    "LeftDetection": ("left_object", None, "warning"),
    "FaceDetection": ("face", "person", "info"),
}

_HEAD_RE = re.compile(r"Code=(?P<code>[^;\s]+);action=(?P<action>\w+)(?:;index=(?P<index>\d+))?")


@dataclass
class DahuaEvent:
    code: str
    action: str
    index: int | None
    data: dict[str, Any]


def parse_dahua_part(body: bytes) -> DahuaEvent | None:
    """Parse one eventManager part. Returns None for unrecognised text."""
    text = body.decode("utf-8", errors="replace").strip()
    match = _HEAD_RE.search(text)
    if not match:
        return None
    data: dict[str, Any] = {}
    marker = text.find("data=", match.end())
    if marker >= 0:
        raw = text[marker + len("data="):].strip()
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                data = loaded
        except json.JSONDecodeError:
            data = {}
    index = match.group("index")
    return DahuaEvent(
        code=match.group("code"),
        action=match.group("action"),
        index=int(index) if index is not None else None,
        data=data,
    )


def dahua_to_event(ev: DahuaEvent, site_id: str, device_id: str) -> Event | None:
    """Map a parsed Dahua event to an Event. Unknown codes are dropped."""
    mapping = CODE_MAP.get(ev.code)
    if mapping is None:
        return None
    kind, label, severity = mapping
    utc_value = ev.data.get("UTC")
    if isinstance(utc_value, (int, float)) and not isinstance(utc_value, bool):
        ts = datetime.fromtimestamp(float(utc_value), tz=timezone.utc)
        ts_source = "source"
    else:
        ts = utcnow()
        ts_source = "arrival"
    attributes: dict[str, Any] = {
        "source_event_code": ev.code,
        "event_state": ev.action.lower(),
        "ts_source": ts_source,
    }
    if ev.index is not None:
        attributes["channel_index"] = ev.index
        if ev.code == "AlarmLocal":
            attributes["io_id"] = str(ev.index)
    obj = ev.data.get("Object")
    if isinstance(obj, dict):
        name = obj.get("Name") or obj.get("Text")
        if name:
            attributes["object_name"] = str(name)
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


class DahuaEventAdapter(ThreadedAdapter):
    """Attaches to Dahua eventManager and emits native Events."""

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
        codes: str = "[All]",
        read_timeout: float = 60.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(site_id, device_id, emit, **kwargs)
        self._base_url = (base_url or f"{scheme}://{host}:{port}").rstrip("/")
        self._username = username
        self._password = password
        self._codes = codes
        self._read_timeout = read_timeout

    def _session(self) -> None:
        url = f"{self._base_url}/cgi-bin/eventManager.cgi"
        params = {"action": "attach", "codes": self._codes}
        timeout = httpx.Timeout(10.0, read=self._read_timeout)
        auth = httpx.DigestAuth(self._username, self._password)
        with httpx.Client(auth=auth, timeout=timeout) as client:
            with client.stream("GET", url, params=params) as resp:
                resp.raise_for_status()
                boundary = boundary_from_content_type(resp.headers.get("content-type", ""))
                if boundary is None:
                    raise ValueError("eventManager response has no multipart boundary")
                splitter = MultipartSplitter(boundary)
                self._set_connected(True)
                for chunk in resp.iter_bytes():
                    if self._stop_requested:
                        return
                    for part in splitter.feed(chunk):
                        self._handle_body(part.body)

    def _handle_body(self, body: bytes) -> None:
        ev = parse_dahua_part(body)
        if ev is None:
            return
        event = dahua_to_event(ev, self.site_id, self.device_id)
        if event is not None:
            self._emit_event(event)
