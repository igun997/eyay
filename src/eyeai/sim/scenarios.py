"""Deterministic scenarios that drive every rule through `eyeai.ingest.Ingest`.

A scenario is a timeline of steps. An `Event` is handed to `Ingest.handle`; a
`datetime` is handed to `Ingest.tick`. Times are written in Asia/Jakarta local time and
converted to UTC, so the opening-hours boundaries are exercised for real. Nothing here
reads the wall clock or touches the network, and every event id is fixed, so two runs
of the same scenario produce identical output.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from eyeai.rules.config import DeviceHealthCfg, RulesConfig, load_config
from eyeai.schema import Event, Provenance

EXAMPLE_CONFIG = Path(__file__).resolve().parents[3] / "config" / "rules.example.yaml"
JAKARTA = ZoneInfo("Asia/Jakarta")

# 2026-05-04 is a Monday. The example schedule is open Mon-Sat 08:00-18:00 local.
MONDAY = (2026, 5, 4)

Step = Event | datetime


def local(hour: int, minute: int = 0, second: int = 0, day: tuple[int, int, int] = MONDAY) -> datetime:
    """A Jakarta wall-clock time as a tz-aware UTC datetime."""
    return datetime(*day, hour, minute, second, tzinfo=JAKARTA).astimezone(timezone.utc)


def _cfg() -> RulesConfig:
    return load_config(EXAMPLE_CONFIG)


class _Timeline:
    """Collects events and ticks, then orders them by time and assigns fixed ids."""

    def __init__(self, name: str, cfg: RulesConfig) -> None:
        self.name = name
        self.cfg = cfg
        self._pending: list[tuple[datetime, int, Step]] = []

    def event(
        self,
        ts: datetime,
        device: str,
        kind: str,
        provenance: Provenance,
        *,
        label: str | None = None,
        zone: str | None = None,
        track_id: int | None = None,
        severity: str = "info",
        confidence: float = 1.0,
        **attributes: object,
    ) -> None:
        event = Event(
            id="pending",
            ts_utc=ts,
            site_id=self.cfg.site_id,
            device_id=device,
            kind=kind,
            label=label,
            confidence=confidence,
            provenance=provenance,
            track_id=track_id,
            zone=zone,
            severity=severity,
            attributes=dict(attributes),
        )
        self._pending.append((ts, len(self._pending), event))

    def tick(self, ts: datetime) -> None:
        self._pending.append((ts, len(self._pending), ts))

    def build(self) -> tuple[Step, ...]:
        ordered = sorted(self._pending, key=lambda item: (item[0], item[1]))
        steps: list[Step] = []
        for index, (_, _, step) in enumerate(ordered, 1):
            if isinstance(step, Event):
                step = step.model_copy(update={"id": f"{self.name}-{index:04d}"})
            steps.append(step)
        return tuple(steps)


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    cfg: RulesConfig
    steps: tuple[Step, ...]
    expect: tuple[str, ...]  # derived kinds this scenario is built to produce


def warehouse_dock_day() -> Scenario:
    """Gate ANPR reconciled against WMS orders: a matched arrival, a no-show and an unknown plate."""
    cfg = _cfg()
    tl = _Timeline("dock", cfg)
    tl.event(local(8, 10), "wms-1", "wms_order", Provenance.EXTERNAL, label="order",
             external_id="ORD-1001", data={"plate": "B 1234 XY"})
    tl.event(local(8, 12), "cam-gate-01", "anpr", Provenance.NATIVE, label="vehicle",
             confidence=0.93, plate="B1234XY")
    tl.event(local(9, 0), "wms-1", "wms_order", Provenance.EXTERNAL, label="order",
             external_id="ORD-1002", data={"plate": "D 5678 AB"})
    tl.event(local(9, 30), "cam-gate-01", "anpr", Provenance.NATIVE, label="vehicle",
             confidence=0.88, plate="F9999ZZ")
    tl.event(local(10, 0), "wms-1", "wms_order", Provenance.EXTERNAL, label="order",
             external_id="ORD-1003", data={"plate": "H 4321 KL"})
    tl.event(local(10, 3), "cam-gate-01", "anpr", Provenance.NATIVE, label="vehicle",
             confidence=0.9, plate="H4321KL")
    tl.tick(local(11, 0))
    return Scenario(
        name="warehouse_dock_day",
        description="gate ANPR vs WMS orders: one match, one no-show, one unknown plate",
        cfg=cfg,
        steps=tl.build(),
        expect=("dock_no_show", "dock_unrecognised"),
    )


def after_hours_intrusion() -> Scenario:
    """Intrusion on the floor at 21:30 (closed) alerts once; the same hour's line cross does not."""
    cfg = _cfg()
    tl = _Timeline("afterhours", cfg)
    tl.event(local(10, 0), "cam-floor-01", "line_cross", Provenance.NATIVE, label="person")
    tl.event(local(21, 30), "cam-floor-01", "intrusion", Provenance.NATIVE, label="person",
             severity="warning", confidence=0.91)
    tl.event(local(21, 31, 20), "cam-floor-01", "intrusion", Provenance.NATIVE, label="person",
             severity="warning", confidence=0.9)
    tl.tick(local(22, 0))
    return Scenario(
        name="after_hours_intrusion",
        description="intrusion at 21:30 local, outside opening hours, repeated within cooldown",
        cfg=cfg,
        steps=tl.build(),
        expect=("after_hours_access",),
    )


def cold_chain_breach() -> Scenario:
    """Cold store rises above 8 C for over 5 minutes, recovers, then the sensor goes silent."""
    cfg = _cfg()
    tl = _Timeline("cold", cfg)
    for minute in range(0, 31):
        value = 9.5 if 10 <= minute <= 20 else 4.0 + 0.1 * (minute % 3)
        tl.event(local(8, 0) + timedelta(minutes=minute), "sensor-cold-01", "temperature",
                 Provenance.SENSOR, label="temperature", value=value, unit="C")
    tl.tick(local(9, 0))
    return Scenario(
        name="cold_chain_breach",
        description="cold store 9.5 C for 10 minutes, back to 4 C, then the sensor stops reporting",
        cfg=cfg,
        steps=tl.build(),
        expect=("cold_chain_breach", "cold_chain_recovered", "sensor_stale"),
    )


def retail_queue_rush() -> Scenario:
    """Checkout queue at or above 8 for two minutes, then it clears."""
    cfg = _cfg()
    tl = _Timeline("queue", cfg)
    counts = [3, 4, 5, 7, 9, 10, 11, 11, 12, 10, 9, 8, 6, 4, 3]
    for index, value in enumerate(counts):
        tl.event(local(10, 0) + timedelta(seconds=30 * index), "sensor-count-01", "count",
                 Provenance.SENSOR, label="queue", value=value, unit="persons")
    tl.tick(local(10, 15))
    return Scenario(
        name="retail_queue_rush",
        description="checkout queue at 8+ people from 10:02, cleared by 10:08",
        cfg=cfg,
        steps=tl.build(),
        expect=("queue_long",),
    )


def machine_vibration_event() -> Scenario:
    """Steady vibration baseline, then three consecutive spikes on the machine hall sensor."""
    cfg = _cfg()
    tl = _Timeline("machine", cfg)
    for i in range(40):
        value = 2.0 + 0.05 * math.sin(i)
        tl.event(local(13, 0) + timedelta(seconds=10 * i), "sensor-vib-01", "vibration",
                 Provenance.SENSOR, label="vibration", value=value, unit="mm/s")
    for k in range(3):
        tl.event(local(13, 0) + timedelta(seconds=400 + 10 * k), "sensor-vib-01", "vibration",
                 Provenance.SENSOR, label="vibration", value=9.0, unit="mm/s")
    tl.tick(local(13, 10))
    return Scenario(
        name="machine_vibration_event",
        description="vibration baseline near 2 mm/s, then three 9 mm/s readings in a row",
        cfg=cfg,
        steps=tl.build(),
        expect=("machine_anomaly",),
    )


def gate_anpr_mismatch() -> Scenario:
    """One OCR error within tolerance (matches) and one plate three edits away (does not)."""
    cfg = _cfg()
    tl = _Timeline("ocr", cfg)
    tl.event(local(8, 0), "wms-1", "wms_order", Provenance.EXTERNAL, label="order",
             external_id="ORD-2001", data={"plate": "B 1234 XY"})
    tl.event(local(8, 2), "cam-gate-01", "anpr", Provenance.NATIVE, label="vehicle",
             confidence=0.71, plate="B1234XV")
    tl.event(local(9, 0), "wms-1", "wms_order", Provenance.EXTERNAL, label="order",
             external_id="ORD-2002", data={"plate": "K 7777 LM"})
    tl.event(local(9, 2), "cam-gate-01", "anpr", Provenance.NATIVE, label="vehicle",
             confidence=0.6, plate="K7779XX")
    tl.tick(local(10, 0))
    return Scenario(
        name="gate_anpr_mismatch",
        description="B1234XV matches B1234XY (one edit); K7779XX matches nothing (three edits)",
        cfg=cfg,
        steps=tl.build(),
        expect=("dock_unrecognised", "dock_no_show"),
    )


def energy_waste_night() -> Scenario:
    """Floor load at 2.5 kW for two hours with no person seen."""
    cfg = _cfg()
    tl = _Timeline("energy", cfg)
    for i in range(25):
        tl.event(local(19, 0) + timedelta(minutes=5 * i), "sensor-power-01", "power",
                 Provenance.SENSOR, label="load", value=2500.0, unit="W")
    tl.tick(local(21, 30))
    return Scenario(
        name="energy_waste_night",
        description="2.5 kW on the floor from 19:00 to 21:00 with nobody present",
        cfg=cfg,
        steps=tl.build(),
        expect=("energy_waste",),
    )


def traffic_counting() -> Scenario:
    """Line crossings bucketed per hour per device and label."""
    cfg = _cfg()
    tl = _Timeline("traffic", cfg)
    for minute in (5, 20, 40):
        tl.event(local(10, minute), "cam-floor-01", "line_cross", Provenance.NATIVE, label="person")
    for minute in (10, 50):
        tl.event(local(11, minute), "cam-floor-01", "line_cross", Provenance.NATIVE, label="person")
    tl.event(local(10, 30), "cam-gate-01", "line_cross", Provenance.NATIVE, label="vehicle")
    tl.tick(local(13, 0))
    return Scenario(
        name="traffic_counting",
        description="hourly line-crossing counts: floor persons 10:00 (3) and 11:00 (2); gate vehicles 10:00 (1)",
        cfg=cfg,
        steps=tl.build(),
        expect=("count_bucket",),
    )


def camera_outage() -> Scenario:
    """The gate camera stops reporting at 08:30 and returns at 09:10."""
    cfg = _cfg().model_copy(
        update={"device_health": DeviceHealthCfg(expected_devices=["cam-gate-01", "cam-floor-01"], stale_s=120)}
    )
    tl = _Timeline("outage", cfg)
    for minute in range(0, 31):
        tl.event(local(8, 0) + timedelta(minutes=minute), "cam-gate-01", "object", Provenance.NATIVE,
                 label="vehicle", confidence=0.9)
    tl.event(local(9, 10), "cam-gate-01", "object", Provenance.NATIVE, label="vehicle", confidence=0.9)
    for minute in range(0, 91):
        tl.event(local(8, 0) + timedelta(minutes=minute), "cam-floor-01", "object", Provenance.NATIVE,
                 label="person", confidence=0.9)
    tl.tick(local(8, 45))
    tl.tick(local(9, 30))
    return Scenario(
        name="camera_outage",
        description="gate camera silent from 08:30 to 09:10; floor camera keeps reporting",
        cfg=cfg,
        steps=tl.build(),
        expect=("device_offline", "device_online"),
    )


def duplicate_native_local() -> Scenario:
    """A native line cross and a local-model detection of the same crossing are counted once."""
    cfg = _cfg()
    tl = _Timeline("dup", cfg)
    tl.event(local(10, 0, 0), "cam-floor-01", "line_cross", Provenance.NATIVE, label="person")
    tl.event(local(10, 0, 2), "cam-floor-01", "line_cross", Provenance.LOCAL_MODEL, label="person",
             confidence=0.8)
    tl.event(local(10, 0, 30), "cam-floor-01", "line_cross", Provenance.LOCAL_MODEL, label="person",
             confidence=0.8)
    tl.event(local(10, 0, 32), "cam-floor-01", "line_cross", Provenance.LOCAL_MODEL, label="person",
             confidence=0.8)
    tl.event(local(10, 0, 33), "cam-floor-01", "line_cross", Provenance.NATIVE, label="person")
    tl.tick(local(11, 5))
    return Scenario(
        name="duplicate_native_local",
        description="native + local detections within 5 s collapse; the 10:00 bucket counts 3",
        cfg=cfg,
        steps=tl.build(),
        expect=("count_bucket",),
    )


def local_tracking_dwell() -> Scenario:
    """Local tracker output at checkout: one shopper dwells 6+ minutes, 13 people exceed capacity 12."""
    cfg = _cfg()
    tl = _Timeline("track", cfg)
    device = "cam-checkout-trk"
    for seconds in range(0, 381, 20):
        tl.event(local(10, 0) + timedelta(seconds=seconds), device, "object", Provenance.LOCAL_MODEL,
                 label="person", zone="checkout", track_id=1, confidence=0.85)
    for track in range(2, 14):
        tl.event(local(10, 0, 10 + track - 2), device, "object", Provenance.LOCAL_MODEL,
                 label="person", zone="checkout", track_id=track, confidence=0.8)
    tl.tick(local(10, 8))
    return Scenario(
        name="local_tracking_dwell",
        description="track 1 dwells 6m20s at checkout; 13 distinct people push occupancy over 12",
        cfg=cfg,
        steps=tl.build(),
        expect=("dwell", "overcapacity"),
    )


SCENARIOS: dict[str, Callable[[], Scenario]] = {
    "warehouse_dock_day": warehouse_dock_day,
    "after_hours_intrusion": after_hours_intrusion,
    "cold_chain_breach": cold_chain_breach,
    "retail_queue_rush": retail_queue_rush,
    "machine_vibration_event": machine_vibration_event,
    "gate_anpr_mismatch": gate_anpr_mismatch,
    "energy_waste_night": energy_waste_night,
    "traffic_counting": traffic_counting,
    "camera_outage": camera_outage,
    "duplicate_native_local": duplicate_native_local,
    "local_tracking_dwell": local_tracking_dwell,
}


def get_scenario(name: str) -> Scenario:
    try:
        factory = SCENARIOS[name]
    except KeyError as exc:
        raise KeyError(f"unknown scenario {name!r}; choose from {', '.join(sorted(SCENARIOS))}") from exc
    return factory()
