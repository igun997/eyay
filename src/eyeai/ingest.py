"""Ingest: the single entry point from adapters into the rule engine.

`Ingest.handle` takes one normalised event and returns the RULE events it caused.
`Ingest.tick` advances time without an event (idle periods, live timers).
Time is the event-time watermark (the newest timestamp seen or passed to `tick`),
never the wall clock, so replays are deterministic.

Per event: zone mapping -> plausibility and lateness checks -> NATIVE/LOCAL duplicate
suppression -> time advance (rules' on_tick) -> store -> rules' on_event. Derived
events are stored, audited and fed back to the rules (bounded depth). A rule that
raises is isolated: the error is audited and emitted as a `rule_error` event, and
the other rules still run. Nothing here actuates anything.
"""
from __future__ import annotations

import math
import threading
from collections import Counter
from datetime import datetime, timedelta, timezone

from eyeai.rules import build_rules
from eyeai.rules.base import Ctx, Rule, derive
from eyeai.rules.config import RulesConfig
from eyeai.rules.dedup import Deduplicator
from eyeai.schema import Event, Provenance
from eyeai.store import Store

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_MAX_DEPTH = 3
_PLAUSIBLE_FIELD = {
    "temperature": "temperature_c",
    "power": "power_w",
    "vibration": "vibration_rms",
    "count": "count",
}


class Ingest:
    def __init__(self, store: Store, cfg: RulesConfig) -> None:
        self.store = store
        self.cfg = cfg
        self.ctx = Ctx(cfg=cfg, store=store, _now=_EPOCH)
        self.rules: list[Rule] = build_rules(self.ctx)
        self.stats: Counter[str] = Counter()
        self._dedup = Deduplicator(cfg.dedup)
        self._lock = threading.Lock()

    def handle(self, event: Event) -> list[Event]:
        with self._lock:
            event = self._place(event)
            reason = self._reject_reason(event)
            if reason is not None:
                self.stats["rejected"] += 1
                self.store.audit(
                    "ingest",
                    "rejected",
                    {"event_id": event.id, "kind": event.kind, "device": event.device_id, "reason": reason},
                )
                return []
            if not self._dedup.admit(event):
                self.stats["suppressed"] += 1
                self.store.audit(
                    "rule:dedup",
                    "suppressed",
                    {
                        "event_id": event.id,
                        "kind": event.kind,
                        "device": event.device_id,
                        "provenance": event.provenance.value,
                    },
                )
                return []
            self.stats["accepted"] += 1
            derived = self._advance(event.ts_utc)
            self.store.insert_event(event)
            derived += self._dispatch(event, 0)
            return derived

    def tick(self, now: datetime) -> list[Event]:
        if now.tzinfo is None:
            raise ValueError("tick time must be tz-aware")
        with self._lock:
            return self._advance(now)

    def _place(self, event: Event) -> Event:
        zone = self.cfg.zones_by_device.get(event.device_id)
        if event.zone is None and zone is not None:
            return event.model_copy(update={"zone": zone})
        return event

    def _reject_reason(self, event: Event) -> str | None:
        field = _PLAUSIBLE_FIELD.get(event.kind)
        if event.provenance is Provenance.SENSOR and field is not None:
            raw = event.attributes.get("value")
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                return "missing or non-numeric value"
            value = float(raw)
            if not math.isfinite(value):
                return "non-finite value"
            low, high = getattr(self.cfg.plausibility, field)
            if not low <= value <= high:
                return f"value {value:g} outside plausible range {low:g}..{high:g}"
        now = self.ctx.now()
        if now > _EPOCH and event.ts_utc < now - timedelta(seconds=self.cfg.late_tolerance_s):
            return "older than late_tolerance_s"
        return None

    def _advance(self, ts: datetime) -> list[Event]:
        self.ctx.advance(ts)
        now = self.ctx.now()
        out: list[Event] = []
        for rule in self.rules:
            try:
                derived = rule.on_tick(now)
            except Exception as exc:  # isolate one rule from the others
                derived = [self._failed(rule, exc, now)]
            out += self._publish(derived, 0)
        return out

    def _dispatch(self, event: Event, depth: int) -> list[Event]:
        out: list[Event] = []
        for rule in self.rules:
            if not rule.wants(event):
                continue
            try:
                derived = rule.on_event(event)
            except Exception as exc:
                derived = [self._failed(rule, exc, self.ctx.now())]
            out += self._publish(derived, depth)
        return out

    def _publish(self, events: list[Event], depth: int) -> list[Event]:
        out: list[Event] = []
        for d in events:
            self.store.insert_event(d)
            self.store.audit(
                f"rule:{d.attributes.get('rule', '?')}",
                "fired",
                {"event_id": d.id, "kind": d.kind, "severity": d.severity, "device": d.device_id, "zone": d.zone},
            )
            self.stats["derived"] += 1
            out.append(d)
            if depth < _MAX_DEPTH:
                out += self._dispatch(d, depth + 1)
        return out

    def _failed(self, rule: Rule, exc: Exception, now: datetime) -> Event:
        error = f"{type(exc).__name__}: {exc}"
        self.stats["errors"] += 1
        self.store.audit(f"rule:{rule.id}", "error", {"error": error})
        return derive(
            rule.id,
            "rule_error",
            now,
            site_id=self.cfg.site_id,
            label=rule.id,
            severity="warning",
            attributes={"error": error},
            key=(type(exc).__name__,),
        )
