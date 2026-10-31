"""Build adapter instances from a plain config mapping.

``build_adapters(config, emit)`` validates the mapping and returns adapter
objects. It opens no sockets: the caller starts and stops each object. Every
returned object has ``start()``, ``stop()`` and ``status()``.

Config schema (YAML/JSON-shaped; unknown keys are rejected)::

    site_id: str                     # required
    cameras:                         # optional list
      - device_id: str               # required, unique among cameras
        vendor: isapi | dahua | onvif
        host: str
        username: str
        password: str
        port: int                    # default 80
        scheme: http | https         # default http
        base_url: str                # optional override
        # isapi only:  heartbeat_timeout: float
        # dahua only:  codes: str, read_timeout: float
        # onvif only:  event_url, termination, pull_timeout, message_limit
        # all vendors: backoff_initial, backoff_max
    mqtt_sensors:                    # optional mapping
        device_id: str               # required
        host: str                    # required
        port: int                    # default 1883
        username: str
        password: str
        sensors:                     # required, non-empty
          - sensor_id: str           # required, unique across mqtt_sensors + modbus
            kind: temperature | power | vibration | count | door | machine_state
            topic: str
            zone: str
            value_path: str
            state_path: str
            ts_path: str
            min_value: float
            max_value: float
    modbus:                          # optional list of TCP devices
      - device_id: str               # required
        host: str                    # required
        port: int                    # default 502
        poll_interval: float
        timeout: float
        registers:                   # required, non-empty
          - sensor_id: str           # required, unique across mqtt_sensors + modbus
            kind: str                # required
            address: int             # required
            dtype: u16 | s16 | u32 | s32 | f32
            register_type: holding | input
            word_order: big | little
            unit_id: int
            scale: float
            offset: float
            zone: str
            min_value: float
            max_value: float
            state_map: {int: str}
    frigate:                         # optional mapping
        device_id: str               # default "frigate"
        host: str                    # required
        port: int                    # default 1883
        username: str
        password: str
        topic_prefix: str            # default "frigate"
        cameras: [str]
        frame_sizes: {camera: [width, height]}
        frigate_base_url: str
    external:                        # optional mapping; webhook receiver
        secret: str                  # required
        source_id: str
        host: str                    # default 127.0.0.1
        port: int                    # default 8089
        max_body: int

Shared backoff keys (``backoff_initial``, ``backoff_max``) are accepted by
every ThreadedAdapter section.
"""

from __future__ import annotations

from dataclasses import fields
from typing import Any

from eyeai.adapters.dahua_events import DahuaEventAdapter
from eyeai.adapters.external import WebhookServer
from eyeai.adapters.frigate import FrigateMqttAdapter
from eyeai.adapters.isapi_events import IsapiEventAdapter
from eyeai.adapters.modbus import ModbusTcpAdapter, RegisterSpec
from eyeai.adapters.mqtt_sensor import MqttSensorAdapter, SensorSpec
from eyeai.adapters.onvif_events import OnvifEventAdapter
from eyeai.schema import Emit

_BACKOFF = {"backoff_initial", "backoff_max"}

_TOP_KEYS = {"site_id", "cameras", "mqtt_sensors", "modbus", "frigate", "external"}

_CAMERA_CLASSES: dict[str, type] = {
    "isapi": IsapiEventAdapter,
    "dahua": DahuaEventAdapter,
    "onvif": OnvifEventAdapter,
}

_CAMERA_VENDOR_KEYS: dict[str, set[str]] = {
    "isapi": {"heartbeat_timeout"},
    "dahua": {"codes", "read_timeout"},
    "onvif": {"event_url", "termination", "pull_timeout", "message_limit"},
}

_SENSOR_FIELDS = {f.name for f in fields(SensorSpec)}
_REGISTER_FIELDS = {f.name for f in fields(RegisterSpec)}


def _take(section: str, cfg: Any, allowed: set[str], required: set[str]) -> dict[str, Any]:
    if not isinstance(cfg, dict):
        raise ValueError(f"{section}: expected a mapping, got {type(cfg).__name__}")
    unknown = set(cfg) - allowed
    if unknown:
        raise ValueError(f"{section}: unknown keys {sorted(unknown)}")
    missing = required - set(cfg)
    if missing:
        raise ValueError(f"{section}: missing keys {sorted(missing)}")
    return dict(cfg)


def _as_list(section: str, value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{section}: expected a list")
    return value


def _sensor_spec(section: str, cfg: Any) -> SensorSpec:
    data = _take(section, cfg, _SENSOR_FIELDS, {"sensor_id", "kind", "topic"})
    return SensorSpec(**data)


def _register_spec(section: str, cfg: Any) -> RegisterSpec:
    data = _take(section, cfg, _REGISTER_FIELDS, {"sensor_id", "kind", "address"})
    if "state_map" in data:
        if not isinstance(data["state_map"], dict):
            raise ValueError(f"{section}.state_map: expected a mapping")
        data["state_map"] = {int(k): str(v) for k, v in data["state_map"].items()}
    return RegisterSpec(**data)


def _camera(site_id: str, idx: int, cfg: Any, emit: Emit) -> Any:
    section = f"cameras[{idx}]"
    if not isinstance(cfg, dict):
        raise ValueError(f"{section}: expected a mapping")
    vendor = cfg.get("vendor")
    if vendor not in _CAMERA_CLASSES:
        raise ValueError(f"{section}.vendor: must be one of {sorted(_CAMERA_CLASSES)}, got {vendor!r}")
    allowed = {"device_id", "host", "username", "password", "port", "scheme", "base_url", "vendor"}
    allowed |= _CAMERA_VENDOR_KEYS[vendor] | _BACKOFF
    data = _take(section, cfg, allowed, {"device_id", "vendor", "host", "username", "password"})
    device_id = data.pop("device_id")
    data.pop("vendor")
    return _CAMERA_CLASSES[vendor](site_id, device_id, emit, **data)


def _claim_sensor_ids(section: str, ids: list[str], claimed: set[str]) -> None:
    for sid in ids:
        if sid in claimed:
            raise ValueError(f"{section}: duplicate sensor_id {sid!r}")
        claimed.add(sid)


def _mqtt_sensors(site_id: str, cfg: Any, emit: Emit, claimed: set[str]) -> Any:
    allowed = {"device_id", "host", "port", "username", "password", "sensors"} | _BACKOFF
    data = _take("mqtt_sensors", cfg, allowed, {"device_id", "host", "sensors"})
    device_id = data.pop("device_id")
    specs = [
        _sensor_spec(f"mqtt_sensors.sensors[{i}]", entry)
        for i, entry in enumerate(_as_list("mqtt_sensors.sensors", data.pop("sensors")))
    ]
    _claim_sensor_ids("mqtt_sensors", [s.sensor_id for s in specs], claimed)
    return MqttSensorAdapter(site_id, device_id, emit, sensors=specs, **data)


def _modbus(site_id: str, idx: int, cfg: Any, emit: Emit, claimed: set[str]) -> Any:
    section = f"modbus[{idx}]"
    allowed = {"device_id", "host", "port", "poll_interval", "timeout", "registers"} | _BACKOFF
    data = _take(section, cfg, allowed, {"device_id", "host", "registers"})
    device_id = data.pop("device_id")
    regs = [
        _register_spec(f"{section}.registers[{i}]", entry)
        for i, entry in enumerate(_as_list(f"{section}.registers", data.pop("registers")))
    ]
    _claim_sensor_ids(section, [r.sensor_id for r in regs], claimed)
    return ModbusTcpAdapter(site_id, device_id, emit, registers=regs, **data)


def _frigate(site_id: str, cfg: Any, emit: Emit) -> Any:
    allowed = {
        "device_id", "host", "port", "username", "password",
        "topic_prefix", "cameras", "frame_sizes", "frigate_base_url",
    } | _BACKOFF
    data = _take("frigate", cfg, allowed, {"host"})
    device_id = data.pop("device_id", "frigate")
    if "cameras" in data:
        data["cameras"] = set(_as_list("frigate.cameras", data["cameras"]))
    if "frame_sizes" in data:
        sizes = data["frame_sizes"]
        if not isinstance(sizes, dict):
            raise ValueError("frigate.frame_sizes: expected a mapping of camera -> [width, height]")
        converted: dict[str, tuple[int, int]] = {}
        for cam, size in sizes.items():
            if not isinstance(size, (list, tuple)) or len(size) != 2:
                raise ValueError(f"frigate.frame_sizes.{cam}: expected [width, height]")
            converted[str(cam)] = (int(size[0]), int(size[1]))
        data["frame_sizes"] = converted
    return FrigateMqttAdapter(site_id, device_id, emit, **data)


def _external(site_id: str, cfg: Any, emit: Emit) -> Any:
    allowed = {"secret", "source_id", "host", "port", "max_body"}
    data = _take("external", cfg, allowed, {"secret"})
    return WebhookServer(site_id, emit, **data)


def build_adapters(config: dict[str, Any], emit: Emit) -> list[Any]:
    """Validate ``config`` and return unstarted adapter objects.

    Raises ``ValueError`` for unknown or missing keys, wrong types, unknown
    vendors, or duplicate device or sensor identifiers. Nothing is connected.
    """
    if not isinstance(config, dict):
        raise ValueError("config: expected a mapping")
    unknown = set(config) - _TOP_KEYS
    if unknown:
        raise ValueError(f"config: unknown top-level keys {sorted(unknown)}")
    if "site_id" not in config or not isinstance(config["site_id"], str) or not config["site_id"]:
        raise ValueError("config.site_id: required non-empty string")
    site_id: str = config["site_id"]

    adapters: list[Any] = []
    camera_ids: set[str] = set()
    sensor_ids: set[str] = set()

    for idx, cam in enumerate(_as_list("cameras", config.get("cameras", []))):
        adapter = _camera(site_id, idx, cam, emit)
        if adapter.device_id in camera_ids:
            raise ValueError(f"cameras[{idx}]: duplicate device_id {adapter.device_id!r}")
        camera_ids.add(adapter.device_id)
        adapters.append(adapter)

    if "mqtt_sensors" in config:
        adapters.append(_mqtt_sensors(site_id, config["mqtt_sensors"], emit, sensor_ids))

    for idx, dev in enumerate(_as_list("modbus", config.get("modbus", []))):
        adapters.append(_modbus(site_id, idx, dev, emit, sensor_ids))

    if "frigate" in config:
        adapters.append(_frigate(site_id, config["frigate"], emit))

    if "external" in config:
        adapters.append(_external(site_id, config["external"], emit))

    return adapters
