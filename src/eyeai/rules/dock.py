"""Dock reconciliation: gate ANPR reads vs. expected records from WMS/ERP.

Inputs:
- `anpr` events at the gate zone from a local or native source, with the plate in
  `attributes["plate"]`;
- `wms_order` events with provenance EXTERNAL, plate in `attributes["data"]["plate"]`, and `ts_utc`
  set to the scheduled arrival time.

An arrival and a record pair when their timestamps differ by at most
`match_window_s` and their normalised plates differ by at most `ocr_tolerance`
edits. Unpaired arrivals emit `dock_unrecognised` and unpaired records emit
`dock_no_show`, each once `match_window_s + grace_s` has passed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from eyeai.rules.base import Rule
from eyeai.rules.config import DockCfg
from eyeai.schema import Event, Provenance


def normalise_plate(plate: str) -> str:
    return "".join(ch for ch in plate.upper() if ch.isalnum())


def edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _plate_of(event: Event) -> str | None:
    """Plate of a native ANPR read (`attributes["plate"]`) or an external record (`attributes["data"]["plate"]`)."""
    plate = event.attributes.get("plate")
    if plate is None:
        data = event.attributes.get("data")
        if isinstance(data, dict):
            plate = data.get("plate")
    return plate if isinstance(plate, str) else None


@dataclass(frozen=True)
class _Item:
    ts: datetime
    plate: str
    event_id: str
    device: str


class DockReconciliation(Rule):
    id = "dock_reconciliation"
    kinds = frozenset({"anpr", "wms_order"})

    def __init__(self, ctx, cfg: DockCfg) -> None:
        super().__init__(ctx, cfg)
        self._arrivals: list[_Item] = []
        self._records: list[_Item] = []

    def wants(self, event: Event) -> bool:
        if event.kind == "wms_order":
            return event.provenance is Provenance.EXTERNAL
        return (
            event.kind == "anpr"
            and event.provenance is not Provenance.EXTERNAL
            and event.zone == self.cfg.gate_zone
        )

    def _match(self, item: _Item, pool: list[_Item]) -> _Item | None:
        best: _Item | None = None
        for cand in pool:
            if abs((cand.ts - item.ts).total_seconds()) > self.cfg.match_window_s:
                continue
            if edit_distance(cand.plate, item.plate) > self.cfg.ocr_tolerance:
                continue
            if best is None or (cand.ts, cand.event_id) < (best.ts, best.event_id):
                best = cand
        return best

    def on_event(self, event: Event) -> list[Event]:
        plate = normalise_plate(_plate_of(event) or "")
        if not plate:
            return []
        item = _Item(event.ts_utc, plate, event.id, event.device_id)
        if event.kind == "anpr":
            hit = self._match(item, self._records)
            if hit is not None:
                self._records.remove(hit)
            else:
                self._arrivals.append(item)
        else:
            hit = self._match(item, self._arrivals)
            if hit is not None:
                self._arrivals.remove(hit)
            else:
                self._records.append(item)
        return []

    def on_tick(self, now: datetime) -> list[Event]:
        horizon = self.cfg.match_window_s + self.cfg.grace_s
        out: list[Event] = []
        for item in list(self._arrivals):
            if (now - item.ts).total_seconds() < horizon:
                continue
            self._arrivals.remove(item)
            out.append(
                self.emit(
                    "dock_unrecognised",
                    now,
                    device_id=item.device,
                    label="vehicle",
                    zone=self.cfg.gate_zone,
                    severity="warning",
                    attributes={"plate": item.plate, "gate_time": item.ts.isoformat(), "source_event": item.event_id},
                    key=(item.event_id,),
                )
            )
        for item in list(self._records):
            if (now - item.ts).total_seconds() < horizon:
                continue
            self._records.remove(item)
            out.append(
                self.emit(
                    "dock_no_show",
                    now,
                    device_id=item.device,
                    label="truck",
                    zone=self.cfg.gate_zone,
                    severity="warning",
                    attributes={"plate": item.plate, "expected_at": item.ts.isoformat(), "source_event": item.event_id},
                    key=(item.event_id,),
                )
            )
        return out
