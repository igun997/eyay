from __future__ import annotations

import math
import time
from typing import Any

import pytest

from eyeai.adapters import modbus
from eyeai.adapters.modbus import (
    ModbusTcpAdapter,
    RegisterSpec,
    decode_registers,
    register_value_to_event,
)
from eyeai.schema import Event, Provenance

SITE = "site-1"
DEV = "plc-1"


class _Response:
    def __init__(self, registers: list[int], error: bool = False) -> None:
        self.registers = registers
        self._error = error

    def isError(self) -> bool:  # noqa: N802 - pymodbus API name
        return self._error


class _FakeClient:
    def __init__(self, host: str, *, port: int, timeout: float, retries: int, connect_ok: bool = True) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.retries = retries
        self._connect_ok = connect_ok
        self.connect_calls = 0
        self.closed = False
        self.reads: list[tuple[str, int, int, int]] = []
        self.holding: Any = None
        self.input: Any = None

    def connect(self) -> bool:
        self.connect_calls += 1
        return self._connect_ok

    def read_holding_registers(self, address: int, *, count: int, device_id: int) -> Any:
        self.reads.append(("holding", address, count, device_id))
        return self.holding(address, count) if callable(self.holding) else self.holding

    def read_input_registers(self, address: int, *, count: int, device_id: int) -> Any:
        self.reads.append(("input", address, count, device_id))
        return self.input(address, count) if callable(self.input) else self.input

    def close(self) -> None:
        self.closed = True


class _ClientFactory:
    def __init__(self, **client_kwargs: Any) -> None:
        self.client_kwargs = client_kwargs
        self.clients: list[_FakeClient] = []

    def __call__(self, host: str, *, port: int, timeout: float, retries: int) -> _FakeClient:
        client = _FakeClient(host, port=port, timeout=timeout, retries=retries, **self.client_kwargs)
        self.clients.append(client)
        return client


def _install(monkeypatch: pytest.MonkeyPatch, **client_kwargs: Any) -> _ClientFactory:
    factory = _ClientFactory(**client_kwargs)
    monkeypatch.setattr(modbus, "ModbusTcpClient", factory)
    return factory


def _collector() -> tuple[list[Event], Any]:
    events: list[Event] = []
    return events, events.append


# ----- decode_registers -----------------------------------------------------


def test_decode_u16_and_s16_sign_extension() -> None:
    assert decode_registers([0x1234], "u16", "big") == 0x1234
    assert decode_registers([0xFFFF], "s16", "big") == -1
    assert decode_registers([0x8000], "s16", "big") == -32768
    assert decode_registers([0x7FFF], "s16", "big") == 32767


def test_decode_u32_big_and_little_word_order() -> None:
    assert decode_registers([0x0001, 0x0002], "u32", "big") == 0x10002
    assert decode_registers([0x0002, 0x0001], "u32", "little") == 0x10002


def test_decode_s32_sign_extension() -> None:
    assert decode_registers([0xFFFF, 0xFFFE], "s32", "big") == -2
    assert decode_registers([0x7FFF, 0xFFFF], "s32", "big") == 0x7FFFFFFF
    assert decode_registers([0xFFFE, 0xFFFF], "s32", "little") == -2


def test_decode_f32_big_endian_ieee() -> None:
    # 1.5 is 0x3FC00000 in IEEE 754 binary32.
    assert decode_registers([0x3FC0, 0x0000], "f32", "big") == pytest.approx(1.5)
    # Little word order swaps the two words before decoding.
    assert decode_registers([0x0000, 0x3FC0], "f32", "little") == pytest.approx(1.5)


def test_decode_single_word_ignores_word_order() -> None:
    assert decode_registers([0x00FF], "u16", "little") == 0x00FF


def test_decode_rejects_wrong_register_count() -> None:
    with pytest.raises(ValueError, match=r"expected 2 register\(s\), got 1"):
        decode_registers([0x0001], "u32", "big")
    with pytest.raises(ValueError, match=r"expected 1 register\(s\), got 2"):
        decode_registers([0x0001, 0x0002], "u16", "big")


def test_decode_rejects_word_out_of_range() -> None:
    with pytest.raises(ValueError, match="register word out of range"):
        decode_registers([0x10000], "u16", "big")
    with pytest.raises(ValueError, match="register word out of range"):
        decode_registers([-1, 0], "u32", "big")


# ----- register_value_to_event ----------------------------------------------


def test_numeric_event_applies_scale_and_offset() -> None:
    spec = RegisterSpec(
        sensor_id="t1", kind="temperature", address=10, scale=0.1, offset=-5.0, zone="boiler"
    )
    event = register_value_to_event(spec, 250, site_id=SITE)
    assert event is not None
    assert event.site_id == SITE
    assert event.device_id == "t1"
    assert event.kind == "temperature"
    assert event.label == "t1"
    assert event.provenance is Provenance.SENSOR
    assert event.zone == "boiler"
    assert event.confidence == 1.0
    assert event.attributes["value"] == pytest.approx(20.0)
    assert event.attributes["unit"] == "c"
    assert "implausible" not in event.attributes


def test_numeric_units_per_kind() -> None:
    expected = {"temperature": "c", "power": "w", "vibration": "rms", "count": "count"}
    for kind, unit in expected.items():
        spec = RegisterSpec(sensor_id="s", kind=kind, address=0)
        event = register_value_to_event(spec, 1, site_id=SITE)
        assert event is not None
        assert event.attributes["unit"] == unit


def test_implausible_only_strictly_outside_limits() -> None:
    spec = RegisterSpec(sensor_id="p", kind="power", address=0, min_value=0.0, max_value=50.0)
    high = register_value_to_event(spec, 60, site_id=SITE)
    low = register_value_to_event(spec, -1, site_id=SITE)
    edge = register_value_to_event(spec, 50, site_id=SITE)
    assert high is not None and high.attributes["implausible"] is True
    assert low is not None and low.attributes["implausible"] is True
    assert edge is not None and "implausible" not in edge.attributes


def test_non_finite_numeric_value_is_dropped() -> None:
    spec = RegisterSpec(sensor_id="p", kind="power", address=0)
    assert register_value_to_event(spec, float("nan"), site_id=SITE) is None
    assert register_value_to_event(spec, float("inf"), site_id=SITE) is None


def test_state_event_maps_known_and_unknown_codes() -> None:
    spec = RegisterSpec(
        sensor_id="door-1",
        kind="door",
        address=3,
        zone="dock",
        state_map={0: "closed", 1: "open"},
    )
    opened = register_value_to_event(spec, 1, site_id=SITE)
    assert opened is not None
    assert opened.attributes["state"] == "open"
    assert opened.attributes["raw"] == 1
    assert opened.zone == "dock"

    unknown = register_value_to_event(spec, 7, site_id=SITE)
    assert unknown is not None
    assert unknown.attributes["state"] == "unmapped:7"
    assert unknown.attributes["raw"] == 7


def test_event_ts_source_is_always_arrival() -> None:
    spec = RegisterSpec(sensor_id="c", kind="count", address=0)
    event = register_value_to_event(spec, 4, site_id=SITE)
    assert event is not None
    assert event.attributes["ts_source"] == "arrival"
    assert event.ts_utc.tzinfo is not None


# ----- RegisterSpec validation ----------------------------------------------


def test_register_spec_rejects_bad_fields() -> None:
    with pytest.raises(ValueError, match="sensor_id must be non-empty"):
        RegisterSpec(sensor_id="", kind="power", address=0)
    with pytest.raises(ValueError, match="unsupported sensor kind"):
        RegisterSpec(sensor_id="x", kind="humidity", address=0)
    with pytest.raises(ValueError, match="unsupported dtype"):
        RegisterSpec(sensor_id="x", kind="power", address=0, dtype="u64")
    with pytest.raises(ValueError, match="register_type must be one of"):
        RegisterSpec(sensor_id="x", kind="power", address=0, register_type="coil")
    with pytest.raises(ValueError, match="word_order must be one of"):
        RegisterSpec(sensor_id="x", kind="power", address=0, word_order="middle")


def test_register_spec_address_bounds() -> None:
    with pytest.raises(ValueError, match="address out of range"):
        RegisterSpec(sensor_id="x", kind="power", address=-1)
    with pytest.raises(ValueError, match="address out of range"):
        RegisterSpec(sensor_id="x", kind="power", address=0xFFFF, dtype="u32")
    # The last two-word slot 0xFFFE..0xFFFF is valid.
    RegisterSpec(sensor_id="x", kind="power", address=0xFFFE, dtype="u32")


def test_state_sensor_constraints() -> None:
    with pytest.raises(ValueError, match="must use u16 or s16"):
        RegisterSpec(sensor_id="d", kind="door", address=0, dtype="u32", state_map={0: "closed"})
    with pytest.raises(ValueError, match="needs a state_map"):
        RegisterSpec(sensor_id="d", kind="door", address=0)


def test_register_spec_min_must_not_exceed_max() -> None:
    with pytest.raises(ValueError, match="min > max"):
        RegisterSpec(sensor_id="p", kind="power", address=0, min_value=10.0, max_value=1.0)


# ----- ModbusTcpAdapter construction ----------------------------------------


def _spec(sensor_id: str = "p1", address: int = 0) -> RegisterSpec:
    return RegisterSpec(sensor_id=sensor_id, kind="power", address=address)


def test_adapter_requires_registers() -> None:
    with pytest.raises(ValueError, match="at least one register is required"):
        ModbusTcpAdapter(SITE, DEV, lambda _e: None, host="10.0.0.5", registers=[])


def test_adapter_rejects_duplicate_sensor_ids() -> None:
    with pytest.raises(ValueError, match="sensor ids must be unique"):
        ModbusTcpAdapter(
            SITE, DEV, lambda _e: None, host="10.0.0.5", registers=[_spec("dup"), _spec("dup", 2)]
        )


def test_adapter_rejects_non_positive_timing() -> None:
    with pytest.raises(ValueError, match="poll_interval and timeout must be positive"):
        ModbusTcpAdapter(
            SITE, DEV, lambda _e: None, host="10.0.0.5", registers=[_spec()], poll_interval=0
        )
    with pytest.raises(ValueError, match="poll_interval and timeout must be positive"):
        ModbusTcpAdapter(SITE, DEV, lambda _e: None, host="10.0.0.5", registers=[_spec()], timeout=0)


def test_adapter_initial_status() -> None:
    adapter = ModbusTcpAdapter(SITE, DEV, lambda _e: None, host="10.0.0.5", registers=[_spec()])
    status = adapter.status()
    assert status["device_id"] == DEV
    assert status["connected"] is False
    assert status["last_event_ts"] is None
    assert status["last_error"] is None


# ----- poll path -------------------------------------------------------------


def _adapter(registers: list[RegisterSpec], emit: Any) -> ModbusTcpAdapter:
    return ModbusTcpAdapter(SITE, DEV, emit, host="10.0.0.5", registers=registers, port=1502)


def test_poll_one_reads_holding_with_unit_id_and_emits(monkeypatch: pytest.MonkeyPatch) -> None:
    factory = _install(monkeypatch)
    events, emit = _collector()
    spec = RegisterSpec(
        sensor_id="flow", kind="vibration", address=20, dtype="u32", unit_id=7, scale=0.5
    )
    adapter = _adapter([spec], emit)
    client = factory("10.0.0.5", port=1502, timeout=3.0, retries=0)
    client.holding = _Response([0x0001, 0x0000])

    adapter._poll_one(client, spec)

    assert client.reads == [("holding", 20, 2, 7)]
    assert len(events) == 1
    assert events[0].device_id == "flow"
    assert events[0].attributes["value"] == pytest.approx(0x10000 * 0.5)


def test_poll_one_uses_input_registers_when_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    factory = _install(monkeypatch)
    events, emit = _collector()
    spec = RegisterSpec(sensor_id="cnt", kind="count", address=5, register_type="input")
    adapter = _adapter([spec], emit)
    client = factory("10.0.0.5", port=1502, timeout=3.0, retries=0)
    client.input = _Response([42])

    adapter._poll_one(client, spec)

    assert client.reads == [("input", 5, 1, 1)]
    assert events[0].attributes["value"] == pytest.approx(42.0)


def test_poll_one_error_response_raises_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    factory = _install(monkeypatch)
    spec = _spec("p1", address=9)
    adapter = _adapter([spec], lambda _e: None)
    client = factory("10.0.0.5", port=1502, timeout=3.0, retries=0)
    client.holding = _Response([], error=True)

    with pytest.raises(ConnectionError, match="modbus read failed for p1 at 9"):
        adapter._poll_one(client, spec)


def test_poll_one_none_response_raises_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    factory = _install(monkeypatch)
    spec = _spec("p1", address=9)
    adapter = _adapter([spec], lambda _e: None)
    client = factory("10.0.0.5", port=1502, timeout=3.0, retries=0)
    client.holding = None

    with pytest.raises(ConnectionError, match="modbus read failed for p1 at 9"):
        adapter._poll_one(client, spec)


def test_decode_error_is_recorded_and_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    factory = _install(monkeypatch)
    events, emit = _collector()
    spec = RegisterSpec(sensor_id="wide", kind="power", address=0, dtype="u32")
    adapter = _adapter([spec], emit)
    client = factory("10.0.0.5", port=1502, timeout=3.0, retries=0)
    client.holding = _Response([0x0001])  # one word where two are expected

    adapter._poll_one(client, spec)

    assert events == []
    assert adapter.status()["last_error"] == "decode wide: expected 2 register(s), got 1"


def test_non_finite_value_emits_nothing_and_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    factory = _install(monkeypatch)
    events, emit = _collector()
    spec = RegisterSpec(sensor_id="p1", kind="power", address=0, dtype="f32")
    adapter = _adapter([spec], emit)
    client = factory("10.0.0.5", port=1502, timeout=3.0, retries=0)
    # 0x7FC00000 is a quiet NaN.
    client.holding = _Response([0x7FC0, 0x0000])

    adapter._poll_one(client, spec)

    assert events == []
    assert adapter.status()["last_error"] is None


# ----- session ---------------------------------------------------------------


def test_session_connect_failure_raises_and_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    factory = _install(monkeypatch, connect_ok=False)
    adapter = _adapter([_spec()], lambda _e: None)

    with pytest.raises(ConnectionError, match=r"modbus connect to 10\.0\.0\.5:1502 failed"):
        adapter._session()

    client = factory.clients[0]
    assert client.connect_calls == 1
    assert client.closed is True
    assert client.host == "10.0.0.5"
    assert client.port == 1502
    assert client.timeout == 3.0
    assert client.retries == 0


def test_session_returns_promptly_when_stop_already_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    factory = _install(monkeypatch)
    adapter = _adapter([_spec()], lambda _e: None)
    adapter._stop_evt.set()

    adapter._session()

    client = factory.clients[0]
    assert client.reads == []
    assert client.closed is True
    assert adapter.status()["connected"] is True


def test_threaded_run_emits_and_stops_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    factory = _install(monkeypatch)
    events, emit = _collector()
    spec = RegisterSpec(sensor_id="p1", kind="power", address=0)
    adapter = ModbusTcpAdapter(
        SITE, DEV, emit, host="10.0.0.5", registers=[spec], poll_interval=0.01, backoff_initial=0.01
    )
    # Every poll answers 12 watts.
    original_connect = factory.__call__

    def _factory(host: str, *, port: int, timeout: float, retries: int) -> _FakeClient:
        client = original_connect(host, port=port, timeout=timeout, retries=retries)
        client.holding = _Response([12])
        return client

    monkeypatch.setattr(modbus, "ModbusTcpClient", _factory)

    adapter.start()
    deadline = time.monotonic() + 5.0
    while not events and time.monotonic() < deadline:
        time.sleep(0.01)
    adapter.stop()

    assert events, "adapter emitted no events within 5s"
    assert events[0].attributes["value"] == pytest.approx(12.0)
    assert math.isfinite(events[0].attributes["value"])
    assert factory.clients and all(c.closed for c in factory.clients)
    assert adapter.status()["connected"] is False
