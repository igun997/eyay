"""Cold-chain monitoring of temperature readings per configured zone.

- `cold_chain_breach` (critical): out of [min_c, max_c] continuously for `sustained_s`.
- `cold_chain_recovered` (info): back in range after a breach.
- `sensor_stale` (warning): no reading for `stale_s`. The reference is the last reading,
  or the first time the rule ran if the sensor never reported.
"""
from __future__ import annotations

from datetime import datetime

from eyeai.rules.base import Rule
from eyeai.rules.config import ColdChainCfg
from eyeai.schema import Event


class ColdChain(Rule):
    id = "cold_chain"
    kinds = frozenset({"temperature"})

    def __init__(self, ctx, cfg: ColdChainCfg) -> None:
        super().__init__(ctx, cfg)
        self._zones = {z.zone: z for z in cfg.zones}
        self._out_since: dict[str, datetime] = {}
        self._breached: set[str] = set()
        self._last: dict[str, datetime] = {}
        self._stale: set[str] = set()
        self._armed: datetime | None = None

    def wants(self, event: Event) -> bool:
        return event.kind == "temperature" and event.zone in self._zones

    def on_event(self, event: Event) -> list[Event]:
        if self._armed is None:
            self._armed = self.ctx.now()
        zone = event.zone
        spec = self._zones[zone]
        value = float(event.attributes["value"])
        self._last[zone] = event.ts_utc
        self._stale.discard(zone)
        if spec.min_c <= value <= spec.max_c:
            self._out_since.pop(zone, None)
            if zone in self._breached:
                self._breached.discard(zone)
                return [
                    self.emit(
                        "cold_chain_recovered",
                        event.ts_utc,
                        zone=zone,
                        label="temperature",
                        attributes={"value": value},
                        key=(zone,),
                    )
                ]
            return []
        since = self._out_since.setdefault(zone, event.ts_utc)
        if zone in self._breached or (event.ts_utc - since).total_seconds() < spec.sustained_s:
            return []
        self._breached.add(zone)
        return [
            self.emit(
                "cold_chain_breach",
                event.ts_utc,
                zone=zone,
                label="temperature",
                severity="critical",
                attributes={
                    "value": value,
                    "min_c": spec.min_c,
                    "max_c": spec.max_c,
                    "out_since": since.isoformat(),
                },
                key=(zone,),
            )
        ]

    def on_tick(self, now: datetime) -> list[Event]:
        if self._armed is None:
            self._armed = self.ctx.now()
        out: list[Event] = []
        for zone, spec in self._zones.items():
            ref = self._last.get(zone, self._armed)
            if zone in self._stale or (now - ref).total_seconds() < spec.stale_s:
                continue
            self._stale.add(zone)
            out.append(
                self.emit(
                    "sensor_stale",
                    now,
                    zone=zone,
                    label="temperature",
                    severity="warning",
                    attributes={"last_reading": ref.isoformat()},
                    key=(zone,),
                )
            )
        return out
