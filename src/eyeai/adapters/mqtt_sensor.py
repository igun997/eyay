"""MQTT sensor adapter: turns configured sensor topics into SENSOR events.

Sensor contract (shared with the Modbus adapter and the rule engine):

* ``provenance`` = ``SENSOR``; ``device_id`` = the configured sensor id.
* ``kind`` is one of ``temperature | power | vibration | count | door | machine_state``.
* Numeric kinds carry ``attributes["value"]`` (finite float) and
  ``attributes["unit"]`` in ``c | w | rms | count``.
* ``door`` and ``machine_state`` carry ``attributes["state"]`` (str).
* ``zone`` comes from the sensor config.
* ``attributes["ts_source"]`` is ``source | source_naive_utc | arrival``.
* ``attributes["implausible"] = True`` only when the value lies outside the
  configured ``min``/``max``. Such events are still emitted, flagged.
* Non-finite values (NaN, inf) and undecodable payloads are dropped, never emitted.

Payload forms: plain text (``"23.5"``, ``"open"``) or JSON object with
``value_path`` / ``state_path`` / ``ts_path`` dotted keys.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from paho.mqtt.client import topic_matches_sub

from ..schema import Emit, Event, Provenance
from ._mqtt import ClientFactory, MqttLink, decode_json_payload, resolve_payload_ts, value_at_path
from .base import ThreadedAdapter

NUMERIC_KINDS = {"temperature": "c", "power": "w", "vibration": "rms", "count": "count"}
STATE_KINDS = {"door", "machine_state"}
ALLOWED_KINDS = set(NUMERIC_KINDS) | STATE_KINDS


@dataclass(frozen=True)
class SensorSpec:
    """One configured sensor. ``unit`` is fixed by ``kind`` and not configurable."""

    sensor_id: str
    kind: str
    topic: str
    zone: str | None = None
    value_path: str | None = None
    state_path: str | None = None
    ts_path: str | None = None
    min_value: float | None = None
    max_value: float | None = None

    def __post_init__(self) -> None:
        if not self.sensor_id:
            raise ValueError("sensor_id must be non-empty")
        if self.kind not in ALLOWED_KINDS:
            raise ValueError(f"unsupported sensor kind {self.kind!r}")
        if not self.topic:
            raise ValueError(f"sensor {self.sensor_id!r} needs a topic")
        if self.min_value is not None and self.max_value is not None and self.min_value > self.max_value:
            raise ValueError(f"sensor {self.sensor_id!r}: min > max")


def _as_finite_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        out = float(value)
    elif isinstance(value, str):
        try:
            out = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return out if math.isfinite(out) else None


def _extract(payload: bytes, path: str | None, default_text: bool) -> Any:
    """Return the raw value: JSON path lookup when given, else the decoded text."""
    if path is None:
        if not default_text:
            return None
        return payload.decode("utf-8", errors="replace").strip()
    data = decode_json_payload(payload)
    if data is None:
        return None
    return value_at_path(data, path)


def sensor_message_to_event(
    spec: SensorSpec,
    payload: bytes,
    *,
    site_id: str,
) -> Event | None:
    """Map one MQTT payload for ``spec`` to an Event, or None if unusable."""
    ts_raw: Any = None
    if spec.ts_path is not None:
        data = decode_json_payload(payload)
        if isinstance(data, dict):
            ts_raw = value_at_path(data, spec.ts_path)
    ts, ts_source = resolve_payload_ts(ts_raw)

    attributes: dict[str, Any] = {"ts_source": ts_source}
    if spec.kind in NUMERIC_KINDS:
        raw = _extract(payload, spec.value_path, default_text=True)
        value = _as_finite_float(raw)
        if value is None:
            return None
        attributes["value"] = value
        attributes["unit"] = NUMERIC_KINDS[spec.kind]
        if (spec.min_value is not None and value < spec.min_value) or (
            spec.max_value is not None and value > spec.max_value
        ):
            attributes["implausible"] = True
    else:
        raw = _extract(payload, spec.state_path, default_text=spec.state_path is None)
        if not isinstance(raw, str) or not raw.strip():
            return None
        attributes["state"] = raw.strip().lower() if spec.kind == "door" else raw.strip()

    return Event(
        site_id=site_id,
        device_id=spec.sensor_id,
        ts_utc=ts,
        kind=spec.kind,
        label=spec.sensor_id,
        confidence=1.0,
        provenance=Provenance.SENSOR,
        zone=spec.zone,
        attributes=attributes,
    )


class MqttSensorAdapter(ThreadedAdapter):
    """Subscribes to sensor topics on one broker and emits SENSOR events.

    ``device_id`` names the gateway/broker connection; each emitted Event carries
    the sensor's own id in ``device_id``.
    """

    def __init__(
        self,
        site_id: str,
        device_id: str,
        emit: Emit,
        *,
        host: str,
        sensors: list[SensorSpec],
        port: int = 1883,
        username: str | None = None,
        password: str | None = None,
        client_factory: ClientFactory | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(site_id, device_id, emit, **kwargs)
        if not sensors:
            raise ValueError("at least one sensor is required")
        ids = [s.sensor_id for s in sensors]
        if len(set(ids)) != len(ids):
            raise ValueError("sensor ids must be unique")
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._sensors = list(sensors)
        self._client_factory = client_factory

    def _session(self) -> None:
        topics = sorted({s.topic for s in self._sensors})
        link = MqttLink(
            client_id=f"eyeai-sensors-{self.device_id}",
            host=self._host,
            port=self._port,
            username=self._username,
            password=self._password,
            topics=topics,
            on_message=self._on_message,
            client_factory=self._client_factory,
        )
        link.run(self._stop_evt, self._set_connected)

    def _on_message(self, topic: str, payload: bytes) -> None:
        for spec in self._sensors:
            if not topic_matches_sub(spec.topic, topic):
                continue
            event = sensor_message_to_event(spec, payload, site_id=self.site_id)
            if event is not None:
                self._emit_event(event)


__all__ = ["SensorSpec", "MqttSensorAdapter", "sensor_message_to_event", "NUMERIC_KINDS", "STATE_KINDS"]
