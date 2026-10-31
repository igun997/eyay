from __future__ import annotations

import re
from typing import Any

import pytest

from eyeai.adapters.dahua_events import DahuaEventAdapter
from eyeai.adapters.external import WebhookServer
from eyeai.adapters.frigate import FrigateMqttAdapter
from eyeai.adapters.isapi_events import IsapiEventAdapter
from eyeai.adapters.modbus import ModbusTcpAdapter
from eyeai.adapters.mqtt_sensor import MqttSensorAdapter
from eyeai.adapters.onvif_events import OnvifEventAdapter
from eyeai.adapters.registry import build_adapters
from eyeai.schema import Event

SITE = "site-1"


def _emit(event: Event) -> None:
    return None


def _camera(device_id: str, vendor: str, **extra: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "device_id": device_id,
        "vendor": vendor,
        "host": "192.0.2.10",
        "username": "admin",
        "password": "pw",
    }
    cfg.update(extra)
    return cfg


def _full_config() -> dict[str, Any]:
    return {
        "site_id": SITE,
        "cameras": [
            _camera("cam-isapi", "isapi", heartbeat_timeout=30.0),
            _camera("cam-dahua", "dahua", codes="All", read_timeout=10.0),
            _camera("cam-onvif", "onvif", event_url="/onvif/events", pull_timeout=5.0),
        ],
        "mqtt_sensors": {
            "device_id": "sensors-1",
            "host": "192.0.2.20",
            "sensors": [
                {"sensor_id": "temp-1", "kind": "temperature", "topic": "plant/temp"},
            ],
        },
        "modbus": [
            {
                "device_id": "plc-1",
                "host": "192.0.2.30",
                "registers": [
                    {"sensor_id": "pwr-1", "kind": "power", "address": 100},
                ],
            },
        ],
        "frigate": {
            "host": "192.0.2.40",
            "cameras": ["front"],
            "frame_sizes": {"front": [1920, 1080]},
        },
        "external": {"secret": "s3cret"},
    }


def test_empty_config_builds_nothing() -> None:
    assert build_adapters({"site_id": SITE}, _emit) == []


def test_full_config_builds_one_adapter_per_section() -> None:
    adapters = build_adapters(_full_config(), _emit)
    assert [type(a) for a in adapters] == [
        IsapiEventAdapter,
        DahuaEventAdapter,
        OnvifEventAdapter,
        MqttSensorAdapter,
        ModbusTcpAdapter,
        FrigateMqttAdapter,
        WebhookServer,
    ]


def test_built_adapters_carry_configured_identity() -> None:
    adapters = build_adapters(_full_config(), _emit)
    by_type = {type(a): a for a in adapters}
    assert by_type[IsapiEventAdapter].device_id == "cam-isapi"
    assert by_type[DahuaEventAdapter].device_id == "cam-dahua"
    assert by_type[OnvifEventAdapter].device_id == "cam-onvif"
    assert by_type[MqttSensorAdapter].device_id == "sensors-1"
    assert by_type[ModbusTcpAdapter].device_id == "plc-1"
    assert by_type[FrigateMqttAdapter].device_id == "frigate"
    assert by_type[WebhookServer].site_id == SITE
    assert by_type[WebhookServer].source_id == "external"


def test_builder_starts_no_sockets() -> None:
    adapters = build_adapters(_full_config(), _emit)
    assert all(a.status()["connected"] is False for a in adapters if hasattr(a, "status"))
    assert adapters[-1].bound_port is None


def test_backoff_keys_are_accepted_for_every_camera_vendor() -> None:
    config = {
        "site_id": SITE,
        "cameras": [
            _camera("c1", "isapi", backoff_initial=0.5, backoff_max=5.0),
            _camera("c2", "dahua", backoff_initial=0.5, backoff_max=5.0),
            _camera("c3", "onvif", backoff_initial=0.5, backoff_max=5.0),
        ],
    }
    assert len(build_adapters(config, _emit)) == 3


def test_camera_port_and_scheme_are_passed_through() -> None:
    config = {"site_id": SITE, "cameras": [_camera("cam", "isapi", port=8443, scheme="https")]}
    (adapter,) = build_adapters(config, _emit)
    assert isinstance(adapter, IsapiEventAdapter)


def test_external_defaults_are_applied() -> None:
    (server,) = build_adapters({"site_id": SITE, "external": {"secret": "k"}}, _emit)
    assert isinstance(server, WebhookServer)
    assert server.source_id == "external"
    assert server.bound_port is None


def test_missing_site_id_is_rejected() -> None:
    with pytest.raises(ValueError, match=re.escape("config.site_id: required non-empty string")):
        build_adapters({}, _emit)


@pytest.mark.parametrize("site_id", ["", 7, None])
def test_bad_site_id_is_rejected(site_id: Any) -> None:
    with pytest.raises(ValueError, match=re.escape("config.site_id: required non-empty string")):
        build_adapters({"site_id": site_id}, _emit)


def test_non_mapping_config_is_rejected() -> None:
    with pytest.raises(ValueError, match="config: expected a mapping"):
        build_adapters(["site-1"], _emit)  # type: ignore[arg-type]


def test_unknown_top_level_key_is_rejected() -> None:
    with pytest.raises(ValueError, match=re.escape("config: unknown top-level keys ['typo']")):
        build_adapters({"site_id": SITE, "typo": 1}, _emit)


def test_cameras_must_be_a_list() -> None:
    with pytest.raises(ValueError, match="cameras: expected a list"):
        build_adapters({"site_id": SITE, "cameras": {"a": 1}}, _emit)


def test_camera_missing_required_key_is_rejected() -> None:
    cfg = _camera("cam", "isapi")
    del cfg["password"]
    with pytest.raises(ValueError, match=re.escape("cameras[0]: missing keys ['password']")):
        build_adapters({"site_id": SITE, "cameras": [cfg]}, _emit)


def test_camera_unknown_vendor_is_rejected() -> None:
    with pytest.raises(ValueError, match=re.escape("cameras[0].vendor: must be one of")):
        build_adapters({"site_id": SITE, "cameras": [_camera("cam", "axis")]}, _emit)


def test_vendor_specific_key_on_wrong_vendor_is_rejected() -> None:
    cfg = _camera("cam", "isapi", codes="All")
    with pytest.raises(ValueError, match=re.escape("cameras[0]: unknown keys ['codes']")):
        build_adapters({"site_id": SITE, "cameras": [cfg]}, _emit)


def test_camera_must_be_a_mapping() -> None:
    with pytest.raises(ValueError, match="cameras\\[0\\]: expected a mapping"):
        build_adapters({"site_id": SITE, "cameras": ["cam"]}, _emit)


def test_duplicate_camera_device_id_is_rejected() -> None:
    config = {
        "site_id": SITE,
        "cameras": [_camera("cam", "isapi"), _camera("cam", "dahua")],
    }
    with pytest.raises(ValueError, match=re.escape("cameras[1]: duplicate device_id 'cam'")):
        build_adapters(config, _emit)


def test_duplicate_sensor_id_across_mqtt_and_modbus_is_rejected() -> None:
    config = {
        "site_id": SITE,
        "mqtt_sensors": {
            "device_id": "s",
            "host": "h",
            "sensors": [{"sensor_id": "x", "kind": "temperature", "topic": "t"}],
        },
        "modbus": [
            {
                "device_id": "m",
                "host": "h2",
                "registers": [{"sensor_id": "x", "kind": "power", "address": 1}],
            },
        ],
    }
    with pytest.raises(ValueError, match=re.escape("modbus[0]: duplicate sensor_id 'x'")):
        build_adapters(config, _emit)


def test_mqtt_sensors_missing_host_is_rejected() -> None:
    config = {
        "site_id": SITE,
        "mqtt_sensors": {"device_id": "s", "sensors": [{"sensor_id": "a", "kind": "door", "topic": "t"}]},
    }
    with pytest.raises(ValueError, match=re.escape("mqtt_sensors: missing keys ['host']")):
        build_adapters(config, _emit)


def test_mqtt_sensors_unknown_key_is_rejected() -> None:
    config = {
        "site_id": SITE,
        "mqtt_sensors": {
            "device_id": "s",
            "host": "h",
            "bogus": 1,
            "sensors": [{"sensor_id": "a", "kind": "door", "topic": "t"}],
        },
    }
    with pytest.raises(ValueError, match=re.escape("mqtt_sensors: unknown keys ['bogus']")):
        build_adapters(config, _emit)


def test_sensor_entry_unknown_key_is_rejected() -> None:
    config = {
        "site_id": SITE,
        "mqtt_sensors": {
            "device_id": "s",
            "host": "h",
            "sensors": [{"sensor_id": "a", "kind": "door", "topic": "t", "flavour": "x"}],
        },
    }
    with pytest.raises(ValueError, match=re.escape("mqtt_sensors.sensors[0]: unknown keys ['flavour']")):
        build_adapters(config, _emit)


def test_sensor_unsupported_kind_is_rejected() -> None:
    config = {
        "site_id": SITE,
        "mqtt_sensors": {
            "device_id": "s",
            "host": "h",
            "sensors": [{"sensor_id": "a", "kind": "humidity", "topic": "t"}],
        },
    }
    with pytest.raises(ValueError, match="unsupported sensor kind 'humidity'"):
        build_adapters(config, _emit)


def test_mqtt_sensors_sensors_must_be_a_list() -> None:
    config = {
        "site_id": SITE,
        "mqtt_sensors": {"device_id": "s", "host": "h", "sensors": {"a": 1}},
    }
    with pytest.raises(ValueError, match="mqtt_sensors.sensors: expected a list"):
        build_adapters(config, _emit)


def test_modbus_register_missing_address_is_rejected() -> None:
    config = {
        "site_id": SITE,
        "modbus": [
            {"device_id": "m", "host": "h", "registers": [{"sensor_id": "a", "kind": "power"}]},
        ],
    }
    with pytest.raises(ValueError, match=re.escape("modbus[0].registers[0]: missing keys ['address']")):
        build_adapters(config, _emit)


def test_modbus_state_map_keys_are_converted_to_int() -> None:
    config = {
        "site_id": SITE,
        "modbus": [
            {
                "device_id": "m",
                "host": "h",
                "registers": [
                    {
                        "sensor_id": "door-1",
                        "kind": "door",
                        "address": 5,
                        "dtype": "u16",
                        "state_map": {"0": "closed", 1: "open"},
                    },
                ],
            },
        ],
    }
    (adapter,) = build_adapters(config, _emit)
    assert isinstance(adapter, ModbusTcpAdapter)


def test_modbus_state_map_must_be_a_mapping() -> None:
    config = {
        "site_id": SITE,
        "modbus": [
            {
                "device_id": "m",
                "host": "h",
                "registers": [{"sensor_id": "a", "kind": "door", "address": 5, "state_map": [1]}],
            },
        ],
    }
    with pytest.raises(ValueError, match=re.escape("modbus[0].registers[0].state_map: expected a mapping")):
        build_adapters(config, _emit)


def test_frigate_frame_sizes_must_be_a_pair() -> None:
    config = {"site_id": SITE, "frigate": {"host": "h", "frame_sizes": {"front": [1920]}}}
    with pytest.raises(ValueError, match=re.escape("frigate.frame_sizes.front: expected [width, height]")):
        build_adapters(config, _emit)


def test_frigate_requires_host() -> None:
    with pytest.raises(ValueError, match=re.escape("frigate: missing keys ['host']")):
        build_adapters({"site_id": SITE, "frigate": {}}, _emit)


def test_external_requires_secret() -> None:
    with pytest.raises(ValueError, match=re.escape("external: missing keys ['secret']")):
        build_adapters({"site_id": SITE, "external": {"port": 9000}}, _emit)


def test_external_unknown_key_is_rejected() -> None:
    with pytest.raises(ValueError, match=re.escape("external: unknown keys ['token']")):
        build_adapters({"site_id": SITE, "external": {"secret": "k", "token": "x"}}, _emit)
