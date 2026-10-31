"""Modbus TCP poller: reads configured registers and emits SENSOR events.

Only Modbus TCP is implemented. Modbus RTU (serial), BACnet, and OPC-UA are NOT
implemented.

Each configured ``RegisterSpec`` is polled every ``poll_interval`` seconds and
follows the sensor contract in ``mqtt_sensor`` (provenance SENSOR, device_id =
sensor id, ``attributes["value"]``/``unit`` for numeric kinds, ``state`` for
door/machine_state, ``implausible`` flag outside min/max). Non-finite decoded
values are dropped. A failed read ends the session, which triggers the adapter's
reconnect-with-backoff.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from typing import Any

from pymodbus.client import ModbusTcpClient

from ..schema import Emit, Event, Provenance
from ._mqtt import resolve_payload_ts
from .base import ThreadedAdapter
from .mqtt_sensor import NUMERIC_KINDS, STATE_KINDS

DTYPE_WORDS = {"u16": 1, "s16": 1, "u32": 2, "s32": 2, "f32": 2}
REGISTER_TYPES = {"holding", "input"}
WORD_ORDERS = {"big", "little"}


@dataclass(frozen=True)
class RegisterSpec:
    sensor_id: str
    kind: str
    address: int
    dtype: str = "u16"
    register_type: str = "holding"
    word_order: str = "big"
    unit_id: int = 1
    scale: float = 1.0
    offset: float = 0.0
    zone: str | None = None
    min_value: float | None = None
    max_value: float | None = None
    state_map: dict[int, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.sensor_id:
            raise ValueError("sensor_id must be non-empty")
        if self.kind not in NUMERIC_KINDS and self.kind not in STATE_KINDS:
            raise ValueError(f"unsupported sensor kind {self.kind!r}")
        if self.dtype not in DTYPE_WORDS:
            raise ValueError(f"unsupported dtype {self.dtype!r}")
        if self.register_type not in REGISTER_TYPES:
            raise ValueError(f"register_type must be one of {sorted(REGISTER_TYPES)}")
        if self.word_order not in WORD_ORDERS:
            raise ValueError(f"word_order must be one of {sorted(WORD_ORDERS)}")
        if self.address < 0 or self.address + DTYPE_WORDS[self.dtype] > 0x10000:
            raise ValueError(f"sensor {self.sensor_id!r}: address out of range")
        if self.kind in STATE_KINDS and self.dtype not in ("u16", "s16"):
            raise ValueError(f"state sensor {self.sensor_id!r} must use u16 or s16")
        if self.kind in STATE_KINDS and not self.state_map:
            raise ValueError(f"state sensor {self.sensor_id!r} needs a state_map")
        if self.min_value is not None and self.max_value is not None and self.min_value > self.max_value:
            raise ValueError(f"sensor {self.sensor_id!r}: min > max")


def decode_registers(registers: list[int], dtype: str, word_order: str) -> float | int:
    """Decode 16-bit register words into a number. Raises ValueError on bad input."""
    words = DTYPE_WORDS[dtype]
    if len(registers) != words:
        raise ValueError(f"expected {words} register(s), got {len(registers)}")
    if any(not 0 <= r <= 0xFFFF for r in registers):
        raise ValueError("register word out of range")
    ordered = list(registers) if word_order == "big" or words == 1 else list(reversed(registers))
    if dtype == "u16":
        return ordered[0]
    if dtype == "s16":
        return ordered[0] - 0x10000 if ordered[0] >= 0x8000 else ordered[0]
    if dtype in ("u32", "s32"):
        value = (ordered[0] << 16) | ordered[1]
        if dtype == "s32" and value >= 0x80000000:
            value -= 0x100000000
        return value
    raw = struct.pack(">HH", ordered[0], ordered[1])
    return struct.unpack(">f", raw)[0]


def register_value_to_event(spec: RegisterSpec, raw: float | int, *, site_id: str) -> Event | None:
    """Build the sensor Event for an already-decoded raw register value."""
    ts, ts_source = resolve_payload_ts(None)
    attributes: dict[str, Any] = {"ts_source": ts_source}
    if spec.kind in NUMERIC_KINDS:
        value = float(raw) * spec.scale + spec.offset
        if not math.isfinite(value):
            return None
        attributes["value"] = value
        attributes["unit"] = NUMERIC_KINDS[spec.kind]
        if (spec.min_value is not None and value < spec.min_value) or (
            spec.max_value is not None and value > spec.max_value
        ):
            attributes["implausible"] = True
    else:
        key = int(raw)
        attributes["state"] = spec.state_map.get(key, f"unmapped:{key}")
        attributes["raw"] = key
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


class ModbusTcpAdapter(ThreadedAdapter):
    """Polls registers on one Modbus TCP device. ``device_id`` names the device."""

    def __init__(
        self,
        site_id: str,
        device_id: str,
        emit: Emit,
        *,
        host: str,
        registers: list[RegisterSpec],
        port: int = 502,
        poll_interval: float = 5.0,
        timeout: float = 3.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(site_id, device_id, emit, **kwargs)
        if not registers:
            raise ValueError("at least one register is required")
        ids = [r.sensor_id for r in registers]
        if len(set(ids)) != len(ids):
            raise ValueError("sensor ids must be unique")
        if poll_interval <= 0 or timeout <= 0:
            raise ValueError("poll_interval and timeout must be positive")
        self._host = host
        self._port = port
        self._registers = list(registers)
        self._poll_interval = poll_interval
        self._timeout = timeout

    def _session(self) -> None:
        client = ModbusTcpClient(self._host, port=self._port, timeout=self._timeout, retries=0)
        try:
            if not client.connect():
                raise ConnectionError(f"modbus connect to {self._host}:{self._port} failed")
            self._set_connected(True)
            while not self._stop_requested:
                for spec in self._registers:
                    if self._stop_requested:
                        return
                    self._poll_one(client, spec)
                if self._wait(self._poll_interval):
                    return
        finally:
            client.close()

    def _poll_one(self, client: Any, spec: RegisterSpec) -> None:
        count = DTYPE_WORDS[spec.dtype]
        reader = client.read_holding_registers if spec.register_type == "holding" else client.read_input_registers
        response = reader(spec.address, count=count, device_id=spec.unit_id)
        if response is None or response.isError():
            raise ConnectionError(f"modbus read failed for {spec.sensor_id} at {spec.address}")
        registers = list(response.registers)
        try:
            raw = decode_registers(registers, spec.dtype, spec.word_order)
        except ValueError as exc:
            self._set_error(f"decode {spec.sensor_id}: {exc}")
            return
        event = register_value_to_event(spec, raw, site_id=self.site_id)
        if event is not None:
            self._emit_event(event)


__all__ = ["RegisterSpec", "ModbusTcpAdapter", "decode_registers", "register_value_to_event"]
