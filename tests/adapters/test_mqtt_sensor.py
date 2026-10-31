from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Callable

import pytest

from eyeai.adapters.mqtt_sensor import MqttSensorAdapter, SensorSpec, sensor_message_to_event
from eyeai.schema import Event, Provenance
from tests.adapters.mocks.fake_paho import FakeMqttFactory

SITE = "site-1"
DEV = "gw-1"


def _spec(**overrides: Any) -> SensorSpec:
    base: dict[str, Any] = {"sensor_id": "s1", "kind": "temperature", "topic": "plant/t1"}
    base.update(overrides)
    return SensorSpec(**base)


def _wait_for(pred: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met in time")


# --- SensorSpec validation -------------------------------------------------


def test_spec_rejects_empty_sensor_id() -> None:
    with pytest.raises(ValueError, match="sensor_id must be non-empty"):
        _spec(sensor_id="")


def test_spec_rejects_unsupported_kind() -> None:
    with pytest.raises(ValueError, match="unsupported sensor kind 'humidity'"):
        _spec(kind="humidity")


def test_spec_requires_topic() -> None:
    with pytest.raises(ValueError, match="needs a topic"):
        _spec(topic="")


def test_spec_rejects_min_above_max() -> None:
    with pytest.raises(ValueError, match="min > max"):
        _spec(min_value=10.0, max_value=5.0)


def test_spec_accepts_equal_min_and_max() -> None:
    spec = _spec(min_value=5.0, max_value=5.0)
    assert spec.min_value == spec.max_value == 5.0


# --- numeric payloads ------------------------------------------------------


def test_plain_text_temperature_maps_to_sensor_event() -> None:
    event = sensor_message_to_event(_spec(zone="cold-room"), b"23.5", site_id=SITE)
    assert isinstance(event, Event)
    assert event.site_id == SITE
    assert event.device_id == "s1"
    assert event.kind == "temperature"
    assert event.label == "s1"
    assert event.zone == "cold-room"
    assert event.confidence == 1.0
    assert event.provenance is Provenance.SENSOR
    assert event.attributes["value"] == 23.5
    assert event.attributes["unit"] == "c"
    assert event.attributes["ts_source"] == "arrival"
    assert "implausible" not in event.attributes


@pytest.mark.parametrize(
    ("kind", "unit"),
    [("temperature", "c"), ("power", "w"), ("vibration", "rms"), ("count", "count")],
)
def test_numeric_kinds_carry_fixed_unit(kind: str, unit: str) -> None:
    event = sensor_message_to_event(_spec(kind=kind), b"1", site_id=SITE)
    assert event is not None
    assert event.attributes["unit"] == unit


def test_plain_text_with_surrounding_whitespace_is_parsed() -> None:
    event = sensor_message_to_event(_spec(), b"  42 \n", site_id=SITE)
    assert event is not None
    assert event.attributes["value"] == 42.0


def test_json_value_path_extracts_nested_number() -> None:
    payload = json.dumps({"data": {"temp": 7}}).encode()
    event = sensor_message_to_event(_spec(value_path="data.temp"), payload, site_id=SITE)
    assert event is not None
    assert event.attributes["value"] == 7.0


def test_json_value_path_missing_key_is_dropped() -> None:
    payload = json.dumps({"data": {}}).encode()
    assert sensor_message_to_event(_spec(value_path="data.temp"), payload, site_id=SITE) is None


def test_json_value_path_invalid_json_is_dropped() -> None:
    assert sensor_message_to_event(_spec(value_path="temp"), b"not json", site_id=SITE) is None


def test_text_that_is_not_a_number_is_dropped() -> None:
    assert sensor_message_to_event(_spec(), b"hot", site_id=SITE) is None


def test_empty_payload_is_dropped_for_numeric_sensor() -> None:
    assert sensor_message_to_event(_spec(), b"   ", site_id=SITE) is None


@pytest.mark.parametrize("payload", [b"nan", b"NaN", b"inf", b"-inf", b"Infinity"])
def test_non_finite_values_are_dropped(payload: bytes) -> None:
    assert sensor_message_to_event(_spec(), payload, site_id=SITE) is None


def test_json_boolean_is_not_treated_as_number() -> None:
    payload = json.dumps({"temp": True}).encode()
    assert sensor_message_to_event(_spec(value_path="temp"), payload, site_id=SITE) is None


def test_value_above_max_is_emitted_but_flagged_implausible() -> None:
    event = sensor_message_to_event(_spec(max_value=50.0), b"60", site_id=SITE)
    assert event is not None
    assert event.attributes["value"] == 60.0
    assert event.attributes["implausible"] is True


def test_value_below_min_is_flagged_implausible() -> None:
    event = sensor_message_to_event(_spec(min_value=0.0), b"-3", site_id=SITE)
    assert event is not None
    assert event.attributes["implausible"] is True


def test_values_on_range_boundaries_are_not_implausible() -> None:
    low = sensor_message_to_event(_spec(min_value=0.0, max_value=50.0), b"0", site_id=SITE)
    high = sensor_message_to_event(_spec(min_value=0.0, max_value=50.0), b"50", site_id=SITE)
    assert low is not None and high is not None
    assert "implausible" not in low.attributes
    assert "implausible" not in high.attributes


# --- state payloads --------------------------------------------------------


def test_door_text_state_is_lowercased_and_stripped() -> None:
    event = sensor_message_to_event(_spec(kind="door", sensor_id="d1"), b" OPEN ", site_id=SITE)
    assert event is not None
    assert event.kind == "door"
    assert event.attributes["state"] == "open"
    assert "value" not in event.attributes


def test_machine_state_keeps_case() -> None:
    event = sensor_message_to_event(_spec(kind="machine_state"), b"Running", site_id=SITE)
    assert event is not None
    assert event.attributes["state"] == "Running"


def test_empty_state_text_is_dropped() -> None:
    assert sensor_message_to_event(_spec(kind="door"), b"   ", site_id=SITE) is None


def test_state_path_extracts_from_json() -> None:
    payload = json.dumps({"s": {"state": "Closed"}}).encode()
    event = sensor_message_to_event(_spec(kind="door", state_path="s.state"), payload, site_id=SITE)
    assert event is not None
    assert event.attributes["state"] == "closed"


def test_state_path_with_non_json_payload_is_dropped() -> None:
    assert sensor_message_to_event(_spec(kind="door", state_path="s"), b"open", site_id=SITE) is None


def test_state_path_with_non_string_value_is_dropped() -> None:
    payload = json.dumps({"s": 1}).encode()
    assert sensor_message_to_event(_spec(kind="door", state_path="s"), payload, site_id=SITE) is None


# --- timestamps ------------------------------------------------------------


def test_ts_path_with_offset_is_normalised_to_utc() -> None:
    payload = json.dumps({"t": 21.0, "at": "2024-05-01T12:00:00+02:00"}).encode()
    event = sensor_message_to_event(
        _spec(value_path="t", ts_path="at"), payload, site_id=SITE
    )
    assert event is not None
    assert event.ts_utc == datetime(2024, 5, 1, 10, 0, tzinfo=timezone.utc)
    assert event.attributes["ts_source"] == "source"


def test_ts_path_naive_timestamp_is_marked_naive_utc() -> None:
    payload = json.dumps({"t": 21.0, "at": "2024-05-01T12:00:00"}).encode()
    event = sensor_message_to_event(_spec(value_path="t", ts_path="at"), payload, site_id=SITE)
    assert event is not None
    assert event.ts_utc == datetime(2024, 5, 1, 12, 0, tzinfo=timezone.utc)
    assert event.attributes["ts_source"] == "source_naive_utc"


def test_ts_path_with_unusable_value_falls_back_to_arrival() -> None:
    payload = json.dumps({"t": 21.0, "at": "yesterday-ish"}).encode()
    event = sensor_message_to_event(_spec(value_path="t", ts_path="at"), payload, site_id=SITE)
    assert event is not None
    assert event.attributes["ts_source"] == "arrival"
    assert event.ts_utc.tzinfo is not None


def test_ts_path_ignored_for_plain_text_payload() -> None:
    event = sensor_message_to_event(_spec(ts_path="at"), b"21.0", site_id=SITE)
    assert event is not None
    assert event.attributes["ts_source"] == "arrival"


# --- adapter wiring --------------------------------------------------------


def _adapter(sensors: list[SensorSpec], factory: FakeMqttFactory, **kwargs: Any) -> tuple[MqttSensorAdapter, list[Event]]:
    events: list[Event] = []
    adapter = MqttSensorAdapter(
        SITE,
        DEV,
        events.append,
        host="broker.local",
        sensors=sensors,
        client_factory=factory,
        **kwargs,
    )
    return adapter, events


def test_adapter_requires_at_least_one_sensor() -> None:
    with pytest.raises(ValueError, match="at least one sensor is required"):
        MqttSensorAdapter(SITE, DEV, lambda e: None, host="h", sensors=[])


def test_adapter_rejects_duplicate_sensor_ids() -> None:
    sensors = [_spec(sensor_id="x"), _spec(sensor_id="x", topic="other")]
    with pytest.raises(ValueError, match="sensor ids must be unique"):
        MqttSensorAdapter(SITE, DEV, lambda e: None, host="h", sensors=sensors)


def test_adapter_connects_and_subscribes_to_unique_sorted_topics() -> None:
    factory = FakeMqttFactory()
    sensors = [
        _spec(sensor_id="a", topic="plant/b"),
        _spec(sensor_id="b", topic="plant/a"),
        _spec(sensor_id="c", topic="plant/a"),
    ]
    adapter, _ = _adapter(sensors, factory, username="u", password="p")
    adapter.start()
    try:
        client = factory.wait_for_client()
        _wait_for(lambda: adapter.status()["connected"])
        assert client.client_id == f"eyeai-sensors-{DEV}"
        assert client.credentials == ("u", "p")
        assert client.connect_args is not None
        assert client.connect_args[:2] == ("broker.local", 1883)
        assert client.subscriptions == ["plant/a", "plant/b"]
    finally:
        adapter.stop()


def test_adapter_routes_matching_topic_to_sensor_event() -> None:
    factory = FakeMqttFactory()
    sensors = [
        _spec(sensor_id="t1", topic="plant/t1", zone="z1"),
        _spec(sensor_id="door1", kind="door", topic="plant/door1"),
    ]
    adapter, events = _adapter(sensors, factory)
    adapter.start()
    try:
        client = factory.wait_for_client()
        _wait_for(lambda: adapter.status()["connected"])
        client.deliver("plant/t1", b"19.5")
        client.deliver("plant/door1", b"Closed")
        assert [e.device_id for e in events] == ["t1", "door1"]
        assert events[0].attributes["value"] == 19.5
        assert events[0].zone == "z1"
        assert events[1].attributes["state"] == "closed"
        assert adapter.status()["last_event_ts"] is not None
    finally:
        adapter.stop()


def test_adapter_honours_mqtt_wildcards() -> None:
    factory = FakeMqttFactory()
    adapter, events = _adapter([_spec(sensor_id="w", topic="plant/+/temp")], factory)
    adapter.start()
    try:
        client = factory.wait_for_client()
        _wait_for(lambda: adapter.status()["connected"])
        client.deliver("plant/line3/temp", b"30")
        client.deliver("plant/line3/humidity", b"30")
        assert [e.device_id for e in events] == ["w"]
    finally:
        adapter.stop()


def test_adapter_drops_unusable_payloads_without_stopping() -> None:
    factory = FakeMqttFactory()
    adapter, events = _adapter([_spec(sensor_id="t1", topic="plant/t1")], factory)
    adapter.start()
    try:
        client = factory.wait_for_client()
        _wait_for(lambda: adapter.status()["connected"])
        client.deliver("plant/t1", b"nan")
        client.deliver("plant/t1", b"garbage")
        client.deliver("plant/t1", b"12")
        assert [e.attributes["value"] for e in events] == [12.0]
        assert adapter.status()["last_error"] is None
    finally:
        adapter.stop()
