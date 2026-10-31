"""Typed rule configuration (loaded from YAML, validated by pydantic).

All times are local time of `Schedule.timezone` (default Asia/Jakarta, UTC+7,
no DST). Every rule section has `enabled`; unknown keys are rejected so typos
in the YAML fail loudly instead of silently using defaults.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

_WINDOW_RE = re.compile(r"^(\d{2}):(\d{2})-(\d{2}):(\d{2})$")
_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _hhmm_to_min(h: int, m: int) -> int:
    if not (0 <= h <= 24 and 0 <= m < 60) or (h == 24 and m != 0):
        raise ValueError(f"invalid time {h:02d}:{m:02d}")
    return h * 60 + m


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True


class Schedule(BaseModel):
    """Opening hours in local time. Windows are `HH:MM-HH:MM`, end exclusive,
    `24:00` allowed as end. Overnight windows (end < start) are rejected: split
    them into two windows on two days. A day missing from `hours` is closed."""

    model_config = ConfigDict(extra="forbid")
    timezone: str = "Asia/Jakarta"
    hours: dict[str, list[str]] = Field(
        default_factory=lambda: {d: ["08:00-18:00"] for d in _DAYS[:6]}
    )
    _zone: ZoneInfo = PrivateAttr()
    _parsed: dict[int, list[tuple[int, int]]] = PrivateAttr(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> "Schedule":
        self._zone = ZoneInfo(self.timezone)
        parsed: dict[int, list[tuple[int, int]]] = {}
        for day, windows in self.hours.items():
            d = day.lower()
            if d not in _DAYS:
                raise ValueError(f"unknown weekday {day!r}")
            idx = _DAYS.index(d)
            spans = []
            for w in windows:
                m = _WINDOW_RE.match(w.strip())
                if not m:
                    raise ValueError(f"bad window {w!r}, expected HH:MM-HH:MM")
                start = _hhmm_to_min(int(m[1]), int(m[2]))
                end = _hhmm_to_min(int(m[3]), int(m[4]))
                if end <= start:
                    raise ValueError(f"window {w!r} must end after it starts (no overnight windows)")
                spans.append((start, end))
            parsed[idx] = sorted(spans)
        self._parsed = parsed
        return self

    def local(self, ts: datetime) -> datetime:
        if ts.tzinfo is None:
            raise ValueError("timestamp must be tz-aware")
        return ts.astimezone(self._zone)

    def is_open(self, ts: datetime) -> bool:
        local = self.local(ts)
        minutes = local.hour * 60 + local.minute
        return any(s <= minutes < e for s, e in self._parsed.get(local.weekday(), []))


class DedupCfg(_Section):
    """Suppress frame-level repeats of native/local detections and local-model
    detections that a native event already covers. Applies only to NATIVE and
    LOCAL_MODEL events; sensor and external readings are never deduplicated."""

    window_s: float = Field(5.0, gt=0)


class ZoneDwellCfg(_Section):
    min_dwell_s: float = Field(300.0, gt=0)
    zones: list[str] = Field(default_factory=list)  # empty = all zones


class LineCountCfg(_Section):
    bucket_s: int = Field(3600, gt=0)
    grace_s: float = Field(60.0, ge=0)  # close a bucket this long after its end


class OccupancyCfg(_Section):
    capacity: dict[str, int] = Field(default_factory=dict)  # zone -> max people
    presence_ttl_s: float = Field(30.0, gt=0)  # a track unseen this long has left


class AfterHoursCfg(_Section):
    zones: list[str] = Field(default_factory=list)  # empty = all zones
    cooldown_s: float = Field(300.0, ge=0)  # one alert per zone per cooldown


class EnergyWasteCfg(_Section):
    zones: list[str] = Field(default_factory=list)
    watts_threshold: float = Field(2000.0, gt=0)
    sustained_s: float = Field(1800.0, gt=0)
    idle_s: float = Field(1800.0, ge=0)  # no person seen in zone for this long


class DockCfg(_Section):
    gate_zone: str = "gate"
    match_window_s: float = Field(1800.0, gt=0)
    grace_s: float = Field(900.0, ge=0)
    ocr_tolerance: int = Field(1, ge=0, le=3)


class ColdZone(BaseModel):
    model_config = ConfigDict(extra="forbid")
    zone: str
    min_c: float = 2.0
    max_c: float = 8.0
    sustained_s: float = Field(300.0, gt=0)
    stale_s: float = Field(900.0, gt=0)

    @model_validator(mode="after")
    def _check(self) -> "ColdZone":
        if self.min_c >= self.max_c:
            raise ValueError("min_c must be below max_c")
        return self


class ColdChainCfg(_Section):
    zones: list[ColdZone] = Field(default_factory=list)


class MachineCfg(_Section):
    alpha: float = Field(0.05, gt=0, le=1)  # EWMA smoothing factor
    warmup: int = Field(30, ge=2)           # samples before judging
    z_threshold: float = Field(4.0, gt=0)
    consecutive: int = Field(3, ge=1)


class QueueCfg(_Section):
    zones: list[str] = Field(default_factory=list)
    threshold: int = Field(8, ge=1)
    sustained_s: float = Field(120.0, ge=0)


class DeviceHealthCfg(_Section):
    expected_devices: list[str] = Field(default_factory=list)  # empty = every device seen
    stale_s: float = Field(120.0, gt=0)


class EscalationCfg(_Section):
    escalate_after_s: float = Field(600.0, gt=0)


class CorrelatorCfg(_Section):
    window_s: float = Field(300.0, gt=0)
    min_members: int = Field(2, ge=2)


class PlausibilityCfg(BaseModel):
    """Sensor readings outside these ranges are rejected at ingest."""

    model_config = ConfigDict(extra="forbid")
    temperature_c: tuple[float, float] = (-60.0, 80.0)
    power_w: tuple[float, float] = (0.0, 1_000_000.0)
    vibration_rms: tuple[float, float] = (0.0, 1000.0)
    count: tuple[float, float] = (0.0, 10_000.0)

    @field_validator("temperature_c", "power_w", "vibration_rms", "count")
    @classmethod
    def _ordered(cls, v: tuple[float, float]) -> tuple[float, float]:
        if v[0] >= v[1]:
            raise ValueError("range must be (low, high) with low < high")
        return v


class RulesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    site_id: str = "site-1"
    # device_id -> zone. Applied to events that arrive without a zone; native
    # analytics events carry no zone of their own.
    zones_by_device: dict[str, str] = Field(default_factory=dict)
    schedule: Schedule = Field(default_factory=Schedule)
    late_tolerance_s: float = Field(60.0, ge=0)  # older events are rejected at ingest
    plausibility: PlausibilityCfg = Field(default_factory=PlausibilityCfg)
    dedup: DedupCfg = Field(default_factory=DedupCfg)
    zone_dwell: ZoneDwellCfg = Field(default_factory=ZoneDwellCfg)
    line_count: LineCountCfg = Field(default_factory=LineCountCfg)
    occupancy: OccupancyCfg = Field(default_factory=OccupancyCfg)
    after_hours: AfterHoursCfg = Field(default_factory=AfterHoursCfg)
    energy_waste: EnergyWasteCfg = Field(default_factory=EnergyWasteCfg)
    dock_reconciliation: DockCfg = Field(default_factory=DockCfg)
    cold_chain: ColdChainCfg = Field(default_factory=ColdChainCfg)
    machine_anomaly: MachineCfg = Field(default_factory=MachineCfg)
    queue_monitor: QueueCfg = Field(default_factory=QueueCfg)
    device_health: DeviceHealthCfg = Field(default_factory=DeviceHealthCfg)
    escalation: EscalationCfg = Field(default_factory=EscalationCfg)
    incident_correlator: CorrelatorCfg = Field(default_factory=CorrelatorCfg)


def parse_config(data: dict[str, Any]) -> RulesConfig:
    return RulesConfig.model_validate(data or {})


def load_config(path: str | Path) -> RulesConfig:
    with open(path, encoding="utf-8") as fh:
        return parse_config(yaml.safe_load(fh) or {})
