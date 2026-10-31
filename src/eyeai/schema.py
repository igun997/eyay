"""Shared event contract (PDF section 6, 'Arsitektur protokol').

Every source (native camera AI, local model, IoT sensor, rule engine) is
normalised into one `Event`: UTC timestamp, device ID, site ID, confidence,
provenance and correlation ID.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

from pydantic import BaseModel, Field, field_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


class Provenance(str, Enum):
    NATIVE = "native"          # camera/NVR produced the event (ISAPI, ONVIF, Frigate)
    LOCAL_MODEL = "local_model"  # our own detector (gap filling only)
    SENSOR = "sensor"          # MQTT / Modbus / gateway reading
    RULE = "rule"              # derived by the deterministic rule engine
    EXTERNAL = "external"      # POS / WMS / ERP / ANPR record


class Event(BaseModel):
    id: str = Field(default_factory=new_id)
    ts_utc: datetime = Field(default_factory=utcnow)
    site_id: str
    device_id: str                      # camera id, sensor id, rule id, ...
    kind: str                           # e.g. "object", "vehicle", "line_cross", "dwell", "door_open", "alert"
    label: str | None = None            # class label: person, car, truck, forklift ...
    confidence: float = 1.0
    provenance: Provenance
    correlation_id: str | None = None   # ties together events about the same incident
    track_id: int | None = None
    bbox: tuple[float, float, float, float] | None = None  # x1,y1,x2,y2 normalised 0..1
    zone: str | None = None
    severity: str = "info"              # info | warning | critical
    clip_ref: str | None = None         # path/URL of evidence clip or snapshot
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("ts_utc")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("ts_utc must be timezone-aware (UTC)")
        return v.astimezone(timezone.utc)

    @field_validator("confidence")
    @classmethod
    def _conf(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("confidence must be within 0..1")
        return v


# Adapters and detectors call this with each normalised event.
Emit = Callable[[Event], None]
