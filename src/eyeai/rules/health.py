"""Device liveness from the events each device produces.

Only NATIVE and SENSOR events count as a sign of life. The ISAPI adapter discards
heartbeats, so a quiet camera with no analytics events for `stale_s` is reported as
offline. Set `stale_s` above the expected quiet period for each device.

With `expected_devices` set, those devices are monitored from the start, so a device
that never reports is flagged. Without it, every device seen so far is monitored.
"""
from __future__ import annotations

from datetime import datetime

from eyeai.rules.base import Rule
from eyeai.rules.config import DeviceHealthCfg
from eyeai.schema import Event, Provenance


class DeviceHealth(Rule):
    id = "device_health"

    def __init__(self, ctx, cfg: DeviceHealthCfg) -> None:
        super().__init__(ctx, cfg)
        self._last: dict[str, datetime] = {}
        self._offline: set[str] = set()
        self._armed: datetime | None = None

    def wants(self, event: Event) -> bool:
        return event.provenance in (Provenance.NATIVE, Provenance.SENSOR)

    def _monitored(self) -> list[str]:
        names = set(self.cfg.expected_devices) or set(self._last)
        return sorted(names)

    def on_event(self, event: Event) -> list[Event]:
        if self._armed is None:
            self._armed = self.ctx.now()
        self._last[event.device_id] = event.ts_utc
        if event.device_id not in self._offline:
            return []
        self._offline.discard(event.device_id)
        return [
            self.emit(
                "device_online",
                event.ts_utc,
                device_id=event.device_id,
                label="device",
                attributes={},
                key=(event.device_id,),
            )
        ]

    def on_tick(self, now: datetime) -> list[Event]:
        if self._armed is None:
            self._armed = self.ctx.now()
        out: list[Event] = []
        for device in self._monitored():
            if device in self._offline:
                continue
            ref = self._last.get(device, self._armed)
            if (now - ref).total_seconds() < self.cfg.stale_s:
                continue
            self._offline.add(device)
            out.append(
                self.emit(
                    "device_offline",
                    now,
                    device_id=device,
                    label="device",
                    severity="warning",
                    attributes={"last_seen": ref.isoformat()},
                    key=(device,),
                )
            )
        return out
