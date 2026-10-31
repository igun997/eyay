from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
import paho.mqtt.publish as publish
import pytest

from eyeai.adapters._mqtt import (
    MqttLink,
    clamp_unit,
    decode_json_payload,
    default_client_factory,
    resolve_payload_ts,
    value_at_path,
)
from tests.adapters.mocks.fake_paho import FakeMqttFactory

CLIENT_ID = "eyeai-test"
HOST = "broker.local"
PORT = 1883


def _link(
    factory: FakeMqttFactory | None = None,
    *,
    topics: list[str] | None = None,
    on_message: Callable[[str, bytes], None] | None = None,
    username: str | None = None,
    password: str | None = None,
    keepalive: int = 30,
) -> MqttLink:
    kwargs: dict = {}
    if factory is not None:
        kwargs["client_factory"] = factory
    return MqttLink(
        client_id=CLIENT_ID,
        host=HOST,
        port=PORT,
        username=username,
        password=password,
        topics=topics if topics is not None else ["plant/a"],
        on_message=on_message or (lambda _t, _p: None),
        keepalive=keepalive,
        **kwargs,
    )


def _start(
    link: MqttLink,
    stop: threading.Event,
    on_state: Callable[[bool], None],
) -> tuple[threading.Thread, list[BaseException]]:
    errors: list[BaseException] = []

    def _main() -> None:
        try:
            link.run(stop, on_state)
        except BaseException as exc:  # noqa: BLE001 - recorded for the test to assert
            errors.append(exc)

    thread = threading.Thread(target=_main, daemon=True)
    thread.start()
    return thread, errors


def _wait_until(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached before timeout")
        time.sleep(0.01)


def test_link_requires_at_least_one_topic() -> None:
    with pytest.raises(ValueError, match="at least one MQTT topic filter is required"):
        _link(topics=[])


def test_run_subscribes_every_topic_and_tears_down_cleanly() -> None:
    factory = FakeMqttFactory()
    states: list[bool] = []
    stop = threading.Event()
    link = _link(factory, topics=["plant/a", "plant/b"])
    thread, errors = _start(link, stop, states.append)

    client = factory.wait_for_client()
    _wait_until(lambda: states == [True])
    stop.set()
    thread.join(5)

    assert errors == []
    assert client.client_id == CLIENT_ID
    assert client.subscriptions == ["plant/a", "plant/b"]
    assert states == [True, False]
    assert client.disconnected_cleanly is True
    assert client.stopped is True


def test_connect_uses_host_port_and_keepalive() -> None:
    factory = FakeMqttFactory()
    stop = threading.Event()
    stop.set()
    link = _link(factory, keepalive=45)
    link.run(stop, lambda _up: None)

    client = factory.last
    assert client.connect_args == (HOST, PORT, 45)


def test_credentials_are_set_only_when_username_given() -> None:
    stop = threading.Event()
    stop.set()

    with_auth = FakeMqttFactory()
    _link(with_auth, username="user", password="secret").run(stop, lambda _up: None)
    assert with_auth.last.credentials == ("user", "secret")

    anonymous = FakeMqttFactory()
    _link(anonymous).run(stop, lambda _up: None)
    assert anonymous.last.credentials is None


def test_refused_connection_raises_with_reason_code() -> None:
    factory = FakeMqttFactory(connect_rc=5)
    states: list[bool] = []
    stop = threading.Event()
    thread, errors = _start(_link(factory), stop, states.append)
    thread.join(5)

    assert len(errors) == 1
    assert isinstance(errors[0], ConnectionError)
    assert str(errors[0]) == "broker refused connection: 5"
    assert states == [False]
    assert factory.last.stopped is True
    assert factory.last.subscriptions == []


def test_connect_exception_propagates_before_session_starts() -> None:
    factory = FakeMqttFactory(connect_error=OSError("no route to host"))
    states: list[bool] = []
    stop = threading.Event()

    with pytest.raises(OSError, match="no route to host"):
        _link(factory).run(stop, states.append)
    assert states == []


def test_unexpected_drop_raises_connection_lost() -> None:
    factory = FakeMqttFactory()
    states: list[bool] = []
    stop = threading.Event()
    thread, errors = _start(_link(factory), stop, states.append)

    client = factory.wait_for_client()
    _wait_until(lambda: states == [True])
    client.drop(1)
    thread.join(5)

    assert len(errors) == 1
    assert isinstance(errors[0], ConnectionError)
    assert str(errors[0]) == "broker connection lost"
    # Once from the disconnect callback, once from the teardown in finally.
    assert states == [True, False, False]
    assert client.stopped is True


def test_messages_are_passed_to_handler_with_bytes_payload() -> None:
    factory = FakeMqttFactory()
    received: list[tuple[str, bytes]] = []
    stop = threading.Event()
    thread, errors = _start(
        _link(factory, on_message=lambda t, p: received.append((t, p))),
        stop,
        lambda _up: None,
    )

    client = factory.wait_for_client()
    _wait_until(lambda: client.subscriptions == ["plant/a"])
    client.deliver("plant/a", b"raw")
    client.deliver("plant/a", "text")
    stop.set()
    thread.join(5)

    assert errors == []
    assert received == [("plant/a", b"raw"), ("plant/a", b"text")]


def test_handler_exception_is_logged_and_session_continues(
    caplog: pytest.LogCaptureFixture,
) -> None:
    factory = FakeMqttFactory()
    received: list[bytes] = []

    def handler(_topic: str, payload: bytes) -> None:
        if payload == b"boom":
            raise RuntimeError("bad message")
        received.append(payload)

    states: list[bool] = []
    stop = threading.Event()
    thread, errors = _start(_link(factory, on_message=handler), stop, states.append)

    client = factory.wait_for_client()
    _wait_until(lambda: states == [True])
    with caplog.at_level(logging.ERROR):
        client.deliver("plant/a", b"boom")
    client.deliver("plant/a", b"ok")
    stop.set()
    thread.join(5)

    assert errors == []
    assert received == [b"ok"]
    assert "mqtt handler failed for topic plant/a" in caplog.text
    assert states == [True, False]


def test_stop_set_before_run_returns_without_error() -> None:
    factory = FakeMqttFactory()
    states: list[bool] = []
    stop = threading.Event()
    stop.set()

    _link(factory).run(stop, states.append)

    client = factory.last
    assert client.subscriptions == ["plant/a"]
    assert states == [True, False]
    assert client.disconnected_cleanly is True


def test_default_client_factory_builds_paho_client() -> None:
    client = default_client_factory("eyeai-factory")
    assert isinstance(client, mqtt.Client)


def test_decode_json_payload_parses_json_or_returns_stripped_text() -> None:
    assert decode_json_payload(b'{"a": 1}') == {"a": 1}
    assert decode_json_payload(b"  12  ") == 12
    assert decode_json_payload(b"  plain text  ") == "plain text"
    assert decode_json_payload(b"\xff") == "\ufffd"


def test_value_at_path_follows_dotted_keys() -> None:
    data = {"a": {"b": 3}}
    assert value_at_path(data, "a.b") == 3
    assert value_at_path(data, "a.c") is None
    assert value_at_path(data, "a.b.c") is None
    assert value_at_path(data, None) is data
    assert value_at_path(data, "") is data


def test_resolve_payload_ts_reads_epoch_and_iso_values() -> None:
    epoch_dt, marker = resolve_payload_ts(1700000000)
    assert marker == "source"
    assert epoch_dt == datetime.fromtimestamp(1700000000, tz=timezone.utc)

    _, marker = resolve_payload_ts(1700000000.5)
    assert marker == "source"

    offset_dt, marker = resolve_payload_ts("2024-05-01T12:00:00+02:00")
    assert marker == "source"
    assert offset_dt == datetime(2024, 5, 1, 10, 0, tzinfo=timezone.utc)

    _, marker = resolve_payload_ts("2024-05-01T12:00:00Z")
    assert marker == "source"

    _, marker = resolve_payload_ts("2024-05-01T12:00:00")
    assert marker == "source_naive_utc"


def test_resolve_payload_ts_falls_back_to_arrival() -> None:
    for value in (None, True, float("nan"), "not a time", ["list"]):
        _, marker = resolve_payload_ts(value)
        assert marker == "arrival", value


def test_clamp_unit_limits_to_zero_one() -> None:
    assert clamp_unit(-0.5) == 0.0
    assert clamp_unit(1.5) == 1.0
    assert clamp_unit(0.25) == 0.25


@pytest.mark.broker
def test_real_broker_round_trip() -> None:
    host = os.environ.get("EYEAI_MQTT_HOST", "localhost")
    port = int(os.environ.get("EYEAI_MQTT_PORT", "1883"))
    topic = f"eyeai/test/{uuid.uuid4().hex}"
    connected = threading.Event()
    got = threading.Event()
    received: list[tuple[str, bytes]] = []

    def on_message(t: str, p: bytes) -> None:
        received.append((t, p))
        got.set()

    link = MqttLink(
        client_id=f"eyeai-test-{uuid.uuid4().hex[:8]}",
        host=host,
        port=port,
        username=None,
        password=None,
        topics=[topic],
        on_message=on_message,
    )
    stop = threading.Event()
    thread, errors = _start(link, stop, lambda up: connected.set() if up else None)
    try:
        assert connected.wait(10), "adapter never connected to the broker"
        publish.single(topic, b"hello", hostname=host, port=port)
        assert got.wait(10), "published message never arrived"
        assert received == [(topic, b"hello")]
    finally:
        stop.set()
        thread.join(5)
    assert errors == []
