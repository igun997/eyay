"""Frigate NVR adapter: consumes Frigate's MQTT topics and emits NATIVE events.

Frigate publishes (documented topics, default prefix ``frigate``):

* ``<prefix>/events`` — JSON ``{"type": "new"|"update"|"end", "before": {...},
  "after": {...}}`` describing one tracked object (``after.id``, ``after.camera``,
  ``after.label``, ``after.start_time``/``end_time`` as epoch seconds,
  ``after.box`` as pixel ``[x1, y1, x2, y2]``, ``after.entered_zones``,
  ``after.top_score``, ``after.has_clip``, ``after.has_snapshot``).
* ``<prefix>/<camera>/<label>`` — plain integer: current count of that object
  class on that camera.

Mapping (provenance NATIVE; ``device_id`` is the camera name):

* ``new``  -> kind ``object``        (plus ``zone_enter`` per entered zone)
* ``update`` -> kind ``zone_enter`` only for zones newly present versus ``before``
* ``end``  -> kind ``object_end``
* count topic -> kind ``object_count``, emitted only when the count changes

Frigate's ``box`` is in detect-stream pixels. Normalised ``bbox`` is produced only
when the operator supplies the detect resolution per camera (``frame_sizes``);
otherwise ``bbox`` stays ``None`` and the pixel box is kept in attributes.
Clip and snapshot URLs are built only when ``frigate_base_url`` is configured.
"""
from __future__ import annotations

import math
from typing import Any

from ..schema import Emit, Event, Provenance
from ._mqtt import MqttLink, ClientFactory, clamp_unit, decode_json_payload, resolve_payload_ts, value_at_path
from .base import ThreadedAdapter, resolve_source_ts

FrameSize = tuple[int, int]


def _norm_box(box: Any, size: FrameSize | None) -> tuple[float, float, float, float] | None:
    if size is None or not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    w, h = size
    if w <= 0 or h <= 0:
        return None
    try:
        x1, y1, x2, y2 = (float(v) for v in box)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (x1, y1, x2, y2)):
        return None
    return (clamp_unit(x1 / w), clamp_unit(y1 / h), clamp_unit(x2 / w), clamp_unit(y2 / h))


def _confidence(after: dict[str, Any]) -> float:
    for key in ("top_score", "score"):
        raw = after.get(key)
        if isinstance(raw, (int, float)) and not isinstance(raw, bool) and math.isfinite(raw):
            return clamp_unit(float(raw))
    return 1.0


def _zones(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [z for z in value if isinstance(z, str) and z]


def frigate_event_to_events(
    message: dict[str, Any],
    *,
    site_id: str,
    instance: str,
    frame_sizes: dict[str, FrameSize] | None = None,
    base_url: str | None = None,
) -> list[Event]:
    """Map one ``frigate/events`` message to zero or more Events.

    Returns an empty list for messages that do not match the expected shape.
    """
    msg_type = message.get("type")
    after = message.get("after")
    before = message.get("before") if isinstance(message.get("before"), dict) else {}
    if msg_type not in ("new", "update", "end") or not isinstance(after, dict):
        return []
    camera = after.get("camera")
    frigate_id = after.get("id")
    if not isinstance(camera, str) or not camera or not isinstance(frigate_id, str) or not frigate_id:
        return []

    if msg_type == "end":
        raw_ts = value_at_path(after, "end_time") or value_at_path(after, "start_time")
    else:
        raw_ts = value_at_path(after, "start_time")
    ts, ts_source = resolve_payload_ts(raw_ts)
    label = after.get("label") if isinstance(after.get("label"), str) else None
    size = (frame_sizes or {}).get(camera)
    bbox = _norm_box(after.get("box"), size)
    entered_now = _zones(after.get("entered_zones"))
    entered_before = _zones(before.get("entered_zones"))

    base_attrs: dict[str, Any] = {
        "frigate_event_id": frigate_id,
        "frigate_type": msg_type,
        "source_instance": instance,
        "ts_source": ts_source,
        "has_clip": bool(after.get("has_clip")),
        "has_snapshot": bool(after.get("has_snapshot")),
        "false_positive": bool(after.get("false_positive")),
    }
    if after.get("sub_label"):
        base_attrs["sub_label"] = after.get("sub_label")
    if isinstance(after.get("box"), list):
        base_attrs["box_px"] = after.get("box")
    clip_ref = None
    if base_url and after.get("has_clip"):
        clip_ref = f"{base_url.rstrip('/')}/api/events/{frigate_id}/clip.mp4"
    snapshot_ref = None
    if base_url and after.get("has_snapshot"):
        snapshot_ref = f"{base_url.rstrip('/')}/api/events/{frigate_id}/snapshot.jpg"
        base_attrs["snapshot_ref"] = snapshot_ref

    def make(kind: str, zone: str | None, attrs: dict[str, Any], severity: str = "info") -> Event:
        return Event(
            site_id=site_id,
            device_id=camera,
            ts_utc=ts,
            kind=kind,
            label=label,
            confidence=_confidence(after),
            provenance=Provenance.NATIVE,
            correlation_id=frigate_id,
            zone=zone,
            bbox=bbox,
            severity=severity,
            clip_ref=clip_ref,
            attributes={**base_attrs, **attrs},
        )

    events: list[Event] = []
    if msg_type == "new":
        events.append(make("object", None, {}))
        for zone in entered_now:
            events.append(make("zone_enter", zone, {}))
    elif msg_type == "update":
        for zone in entered_now:
            if zone not in entered_before:
                events.append(make("zone_enter", zone, {}))
    else:
        events.append(make("object_end", None, {"end_time": after.get("end_time")}))
    return events


def count_to_event(
    *,
    site_id: str,
    instance: str,
    camera: str,
    label: str,
    count: int,
) -> Event:
    ts, ts_source = resolve_source_ts(None)
    return Event(
        site_id=site_id,
        device_id=camera,
        ts_utc=ts,
        kind="object_count",
        label=label,
        confidence=1.0,
        provenance=Provenance.NATIVE,
        attributes={"count": count, "source_instance": instance, "ts_source": ts_source},
    )


class FrigateMqttAdapter(ThreadedAdapter):
    """Subscribes to a Frigate broker and emits NATIVE object/zone/count events.

    ``device_id`` names this Frigate instance (used for status and attributes).
    Each emitted Event's ``device_id`` is the camera name Frigate reports.
    """

    def __init__(
        self,
        site_id: str,
        device_id: str,
        emit: Emit,
        *,
        host: str,
        port: int = 1883,
        username: str | None = None,
        password: str | None = None,
        topic_prefix: str = "frigate",
        cameras: set[str] | None = None,
        frame_sizes: dict[str, FrameSize] | None = None,
        frigate_base_url: str | None = None,
        client_factory: ClientFactory | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(site_id, device_id, emit, **kwargs)
        if not topic_prefix or topic_prefix.endswith("/"):
            raise ValueError("topic_prefix must be non-empty and must not end with '/'")
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._prefix = topic_prefix
        self._cameras = set(cameras) if cameras else None
        self._frame_sizes = dict(frame_sizes or {})
        self._base_url = frigate_base_url
        self._client_factory = client_factory
        self._counts: dict[tuple[str, str], int] = {}

    def _session(self) -> None:
        link = MqttLink(
            client_id=f"eyeai-frigate-{self.device_id}",
            host=self._host,
            port=self._port,
            username=self._username,
            password=self._password,
            topics=[f"{self._prefix}/events", f"{self._prefix}/+/+"],
            on_message=self._on_message,
            client_factory=self._client_factory,
        )
        link.run(self._stop_evt, self._set_connected)

    def _on_message(self, topic: str, payload: bytes) -> None:
        if not topic.startswith(self._prefix + "/"):
            return
        rest = topic[len(self._prefix) + 1 :]
        if rest == "events":
            self._handle_event(payload)
            return
        parts = rest.split("/")
        if len(parts) == 2:
            self._handle_count(parts[0], parts[1], payload)

    def _handle_event(self, payload: bytes) -> None:
        data = decode_json_payload(payload)
        if not isinstance(data, dict):
            return
        if self._cameras is not None:
            after = data.get("after")
            if not isinstance(after, dict) or after.get("camera") not in self._cameras:
                return
        for event in frigate_event_to_events(
            data,
            site_id=self.site_id,
            instance=self.device_id,
            frame_sizes=self._frame_sizes,
            base_url=self._base_url,
        ):
            self._emit_event(event)

    def _handle_count(self, camera: str, label: str, payload: bytes) -> None:
        if self._cameras is not None and camera not in self._cameras:
            return
        text = payload.decode("utf-8", errors="replace").strip()
        try:
            count = int(text)
        except ValueError:
            return  # motion ON/OFF, audio state, and other non-count topics
        if count < 0:
            return
        key = (camera, label)
        if self._counts.get(key) == count:
            return
        self._counts[key] = count
        self._emit_event(
            count_to_event(
                site_id=self.site_id,
                instance=self.device_id,
                camera=camera,
                label=label,
                count=count,
            )
        )


__all__ = ["FrigateMqttAdapter", "frigate_event_to_events", "count_to_event"]
