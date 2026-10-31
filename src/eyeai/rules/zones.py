"""Zone and presence rules: dwell, occupancy, after-hours access, energy waste, queue length.

Person tracking uses `track_id`. Native analytics in this repo do not set a track id,
so the track-based rules (dwell, occupancy) only fire for events that carry one
(for example from a local tracker). The other rules work on zone-tagged events.
"""
from __future__ import annotations

from datetime import datetime

from eyeai.rules.base import Rule
from eyeai.rules.config import AfterHoursCfg, EnergyWasteCfg, OccupancyCfg, QueueCfg, ZoneDwellCfg
from eyeai.schema import Event, Provenance

_PERSON = "person"


def _in_zones(zones: list[str], zone: str | None) -> bool:
    return zone is not None and (not zones or zone in zones)


class ZoneDwell(Rule):
    """Dwell of a tracked person in a zone. A track stays open until unseen for
    `occupancy.presence_ttl_s`; a `dwell` event is emitted when it closes if it lasted
    at least `min_dwell_s`."""

    id = "zone_dwell"

    def __init__(self, ctx, cfg: ZoneDwellCfg) -> None:
        super().__init__(ctx, cfg)
        self._ttl = ctx.cfg.occupancy.presence_ttl_s
        self._tracks: dict[tuple[str, str, int], list[datetime]] = {}

    def wants(self, event: Event) -> bool:
        return (
            event.label == _PERSON
            and event.track_id is not None
            and _in_zones(self.cfg.zones, event.zone)
        )

    def on_event(self, event: Event) -> list[Event]:
        key = (event.device_id, event.zone, event.track_id)
        span = self._tracks.get(key)
        if span is None:
            self._tracks[key] = [event.ts_utc, event.ts_utc]
        else:
            span[1] = max(span[1], event.ts_utc)
        return []

    def on_tick(self, now: datetime) -> list[Event]:
        out: list[Event] = []
        for key, (first, last) in list(self._tracks.items()):
            if (now - last).total_seconds() < self._ttl:
                continue
            del self._tracks[key]
            dwell = (last - first).total_seconds()
            if dwell < self.cfg.min_dwell_s:
                continue
            device, zone, track = key
            out.append(
                self.emit(
                    "dwell",
                    last,
                    device_id=device,
                    label=_PERSON,
                    zone=zone,
                    track_id=track,
                    attributes={"dwell_s": round(dwell, 1), "first_seen": first.isoformat()},
                    key=key,
                )
            )
        return out


class OccupancyTracker(Rule):
    """Distinct people (tracks seen within `presence_ttl_s`) per zone. Emits
    `overcapacity` once when a zone rises above its capacity."""

    id = "occupancy"

    def __init__(self, ctx, cfg: OccupancyCfg) -> None:
        super().__init__(ctx, cfg)
        self._ttl = cfg.presence_ttl_s
        self._last: dict[tuple[str, int], datetime] = {}
        self._over: set[str] = set()

    def wants(self, event: Event) -> bool:
        return (
            event.label == _PERSON
            and event.track_id is not None
            and event.zone in self.cfg.capacity
        )

    def on_event(self, event: Event) -> list[Event]:
        self._last[(event.zone, event.track_id)] = event.ts_utc
        return self._check(event.zone, event.ts_utc)

    def on_tick(self, now: datetime) -> list[Event]:
        for key, last in list(self._last.items()):
            if (now - last).total_seconds() >= self._ttl:
                del self._last[key]
        out: list[Event] = []
        for zone in sorted(self.cfg.capacity):
            out.extend(self._check(zone, now))
        return out

    def _check(self, zone: str, ts: datetime) -> list[Event]:
        cap = self.cfg.capacity[zone]
        count = sum(
            1
            for (z, _), last in self._last.items()
            if z == zone and (ts - last).total_seconds() < self._ttl
        )
        if count > cap:
            if zone in self._over:
                return []
            self._over.add(zone)
            return [
                self.emit(
                    "overcapacity",
                    ts,
                    label=_PERSON,
                    zone=zone,
                    severity="warning",
                    attributes={"count": count, "capacity": cap},
                    key=(zone,),
                )
            ]
        self._over.discard(zone)
        return []


class AfterHoursAccess(Rule):
    """Access-type detections outside the opening schedule. One alert per zone (or
    device when no zone) per cooldown."""

    id = "after_hours"
    kinds = frozenset({"intrusion", "line_cross", "region_entry", "object", "anpr", "face", "io_input", "door"})

    def __init__(self, ctx, cfg: AfterHoursCfg) -> None:
        super().__init__(ctx, cfg)
        self._last: dict[str, datetime] = {}

    def wants(self, event: Event) -> bool:
        return event.kind in self.kinds and (not self.cfg.zones or event.zone in self.cfg.zones)

    def on_event(self, event: Event) -> list[Event]:
        if self.ctx.cfg.schedule.is_open(event.ts_utc):
            return []
        key = event.zone or event.device_id
        last = self._last.get(key)
        if last is not None and (event.ts_utc - last).total_seconds() < self.cfg.cooldown_s:
            return []
        self._last[key] = event.ts_utc
        return [
            self.emit(
                "after_hours_access",
                event.ts_utc,
                device_id=event.device_id,
                label=event.label or event.kind,
                zone=event.zone,
                severity="critical",
                attributes={"source_kind": event.kind, "source_event": event.id},
                key=(key,),
            )
        ]


class EnergyWaste(Rule):
    """Power at or above the threshold for `sustained_s` while no person has been seen
    in the zone for `idle_s` since the load started. Emits once per high-load episode."""

    id = "energy_waste"

    def __init__(self, ctx, cfg: EnergyWasteCfg) -> None:
        super().__init__(ctx, cfg)
        self._high: dict[str, datetime] = {}
        self._watts: dict[str, float] = {}
        self._person: dict[str, datetime] = {}
        self._fired: set[str] = set()

    def wants(self, event: Event) -> bool:
        if not _in_zones(self.cfg.zones, event.zone):
            return False
        return event.kind == "power" or event.label == _PERSON

    def on_event(self, event: Event) -> list[Event]:
        zone = event.zone
        if event.kind == "power":
            watts = float(event.attributes["value"])
            self._watts[zone] = watts
            if watts >= self.cfg.watts_threshold:
                self._high.setdefault(zone, event.ts_utc)
            else:
                self._high.pop(zone, None)
                self._fired.discard(zone)
        else:
            self._person[zone] = event.ts_utc
        return []

    def on_tick(self, now: datetime) -> list[Event]:
        out: list[Event] = []
        for zone, since in self._high.items():
            if zone in self._fired:
                continue
            if (now - since).total_seconds() < self.cfg.sustained_s:
                continue
            idle_since = max(self._person.get(zone, since), since)
            if (now - idle_since).total_seconds() < self.cfg.idle_s:
                continue
            self._fired.add(zone)
            out.append(
                self.emit(
                    "energy_waste",
                    now,
                    zone=zone,
                    label="power",
                    severity="warning",
                    attributes={"watts": self._watts.get(zone), "high_since": since.isoformat()},
                    key=(zone,),
                )
            )
        return out


class QueueMonitor(Rule):
    """Queue length from `count` sensor readings. Emits `queue_long` once when the
    length stays at or above the threshold for `sustained_s`."""

    id = "queue_monitor"

    def __init__(self, ctx, cfg: QueueCfg) -> None:
        super().__init__(ctx, cfg)
        self._above: dict[str, datetime] = {}
        self._fired: set[str] = set()

    def wants(self, event: Event) -> bool:
        return (
            event.kind == "count"
            and event.provenance is Provenance.SENSOR
            and _in_zones(self.cfg.zones, event.zone)
        )

    def on_event(self, event: Event) -> list[Event]:
        zone = event.zone
        length = float(event.attributes["value"])
        if length < self.cfg.threshold:
            self._above.pop(zone, None)
            self._fired.discard(zone)
            return []
        since = self._above.setdefault(zone, event.ts_utc)
        if zone in self._fired or (event.ts_utc - since).total_seconds() < self.cfg.sustained_s:
            return []
        self._fired.add(zone)
        return [
            self.emit(
                "queue_long",
                event.ts_utc,
                zone=zone,
                label="queue",
                severity="warning",
                attributes={"length": length, "threshold": self.cfg.threshold, "above_since": since.isoformat()},
                key=(zone,),
            )
        ]
