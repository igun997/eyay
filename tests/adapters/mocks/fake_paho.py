"""In-process stand-in for ``paho.mqtt.client.Client`` (paho v2 callback signatures).

Only the surface used by ``eyeai.adapters._mqtt.MqttLink`` is implemented. Tests drive it
with ``deliver`` (inbound publish) and ``drop`` (broker connection loss), and inspect the
recorded calls. No network I/O happens here.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any


class FakeMqttClient:
    def __init__(self, client_id: str, *, connect_rc: Any = 0, connect_error: Exception | None = None) -> None:
        self.client_id = client_id
        self.connect_rc = connect_rc
        self.connect_error = connect_error
        self.on_connect = None
        self.on_disconnect = None
        self.on_message = None
        self.credentials: tuple[str, str | None] | None = None
        self.connect_args: tuple[str, int, int] | None = None
        self.subscriptions: list[str] = []
        self.loop_started = False
        self.disconnected_cleanly = False
        self.stopped = False
        self._lock = threading.Lock()

    def username_pw_set(self, username: str, password: str | None = None) -> None:
        self.credentials = (username, password)

    def connect(self, host: str, port: int, keepalive: int = 60) -> None:
        self.connect_args = (host, port, keepalive)
        if self.connect_error is not None:
            raise self.connect_error

    def loop_start(self) -> None:
        self.loop_started = True
        if self.on_connect is not None:
            self.on_connect(self, None, {}, self.connect_rc, None)

    def subscribe(self, topic: str, qos: int = 0) -> tuple[int, int]:
        with self._lock:
            self.subscriptions.append(topic)
        return (0, 1)

    def disconnect(self) -> None:
        self.disconnected_cleanly = True

    def loop_stop(self) -> None:
        self.stopped = True

    # --- test drivers -------------------------------------------------
    def deliver(self, topic: str, payload: bytes | str) -> None:
        """Simulate an inbound PUBLISH from the broker."""
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        if self.on_message is not None:
            self.on_message(self, None, SimpleNamespace(topic=topic, payload=payload))

    def drop(self, rc: int = 1) -> None:
        """Simulate an unexpected broker disconnect."""
        if self.on_disconnect is not None:
            self.on_disconnect(self, None, {}, rc, None)


class FakeMqttFactory:
    """Callable matching ``client_factory(client_id) -> client``; remembers every client."""

    def __init__(self, **client_kwargs: Any) -> None:
        self._client_kwargs = client_kwargs
        self.clients: list[FakeMqttClient] = []
        self._ready = threading.Event()

    def __call__(self, client_id: str) -> FakeMqttClient:
        client = FakeMqttClient(client_id, **self._client_kwargs)
        self.clients.append(client)
        self._ready.set()
        return client

    @property
    def last(self) -> FakeMqttClient:
        return self.clients[-1]

    def wait_for_client(self, timeout: float = 5.0) -> FakeMqttClient:
        if not self._ready.wait(timeout):
            raise TimeoutError("adapter never created an MQTT client")
        return self.last
