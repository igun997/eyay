"""Shared paho-mqtt (v2 callback API) session runner for MQTT-based adapters.

One `MqttLink.run()` call is one broker connection lifetime. It returns when
the stop event is set and raises `ConnectionError` when the broker refuses the
connection or drops it, so the owning adapter's reconnect loop takes over.
Message handlers run on paho's network thread and must not block.
"""
from __future__ import annotations

import json
import logging
import math
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from .base import resolve_source_ts

log = logging.getLogger("eyeai.adapters.mqtt")

MessageHandler = Callable[[str, bytes], None]
ClientFactory = Callable[[str], Any]


def default_client_factory(client_id: str) -> Any:
    import paho.mqtt.client as mqtt

    return mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=client_id,
        protocol=mqtt.MQTTv311,
    )


def _is_failure(reason_code: Any) -> bool:
    flag = getattr(reason_code, "is_failure", None)
    if flag is not None:
        return bool(flag)
    return bool(reason_code)


class MqttLink:
    """One broker session: connect, subscribe, dispatch messages, tear down."""

    def __init__(
        self,
        *,
        client_id: str,
        host: str,
        port: int,
        username: str | None,
        password: str | None,
        topics: list[str],
        on_message: MessageHandler,
        client_factory: ClientFactory | None = None,
        keepalive: int = 30,
    ) -> None:
        if not topics:
            raise ValueError("at least one MQTT topic filter is required")
        self._client_id = client_id
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._topics = list(topics)
        self._on_message = on_message
        self._factory = client_factory or default_client_factory
        self._keepalive = keepalive

    def run(self, stop_evt: threading.Event, on_state: Callable[[bool], None]) -> None:
        client = self._factory(self._client_id)
        disconnected = threading.Event()
        refusal: list[str] = []

        def _on_connect(c: Any, _ud: Any, _flags: Any, reason_code: Any, *_rest: Any) -> None:
            if _is_failure(reason_code):
                refusal.append(str(reason_code))
                disconnected.set()
                return
            for topic in self._topics:
                c.subscribe(topic, qos=0)
            on_state(True)

        def _on_disconnect(*_args: Any) -> None:
            on_state(False)
            disconnected.set()

        def _on_message(_c: Any, _ud: Any, msg: Any) -> None:
            try:
                self._on_message(msg.topic, bytes(msg.payload))
            except Exception:  # noqa: BLE001 - one bad message must not drop the session
                log.exception("mqtt handler failed for topic %s", msg.topic)

        client.on_connect = _on_connect
        client.on_disconnect = _on_disconnect
        client.on_message = _on_message
        if self._username:
            client.username_pw_set(self._username, self._password)
        client.connect(self._host, self._port, self._keepalive)
        client.loop_start()
        try:
            while not stop_evt.wait(0.5):
                if disconnected.is_set():
                    if refusal:
                        raise ConnectionError(f"broker refused connection: {refusal[0]}")
                    raise ConnectionError("broker connection lost")
        finally:
            try:
                client.disconnect()
            except Exception:  # noqa: BLE001 - teardown is best effort
                log.debug("mqtt disconnect failed", exc_info=True)
            client.loop_stop()
            on_state(False)


def decode_json_payload(payload: bytes) -> Any:
    """Return parsed JSON, or the stripped text when the payload is not JSON."""
    text = payload.decode("utf-8", errors="replace").strip()
    try:
        return json.loads(text)
    except ValueError:
        return text


def value_at_path(data: Any, path: str | None) -> Any:
    """Follow a dotted key path through nested dicts. None path returns data."""
    if not path:
        return data
    cur = data
    for key in path.split("."):
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


def resolve_payload_ts(value: Any) -> tuple[datetime, str]:
    """Source timestamp from a JSON value: epoch seconds or ISO 8601 string."""
    if isinstance(value, bool):
        return resolve_source_ts(None)
    if isinstance(value, (int, float)) and math.isfinite(value):
        return datetime.fromtimestamp(float(value), tz=timezone.utc), "source"
    if isinstance(value, str):
        return resolve_source_ts(value)
    return resolve_source_ts(None)


def clamp_unit(value: float) -> float:
    return min(1.0, max(0.0, value))
