"""Rule base class, evaluation context and helpers.

Rules are deterministic state machines. They receive events in arrival order and
return derived `Event` objects. They never call adapters, never touch the network
and never actuate anything. Time is read only through `Ctx.now()`, the event-time
watermark (newest timestamp seen or passed to `Ingest.tick`), so replays are
reproducible.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from eyeai.rules.config import RulesConfig
from eyeai.schema import Event, Provenance
from eyeai.store import Store

# Fixed namespace so derived ids are stable across runs (deterministic replay).
_NS = uuid.UUID("6f1d2a3c-8e4b-4f5a-9c7d-0e1f2a3b4c5d")


@dataclass
class Ctx:
    """Shared context handed to every rule call."""

    cfg: RulesConfig
    store: Store
    _now: datetime

    def now(self) -> datetime:
        return self._now

    def advance(self, ts: datetime) -> None:
        if ts > self._now:
            self._now = ts


def derived_id(rule_id: str, kind: str, *parts: object) -> str:
    """Deterministic id: same inputs -> same id, so replays compare equal."""
    key = "|".join([rule_id, kind, *(str(p) for p in parts)])
    return str(uuid.uuid5(_NS, key))


def derive(
    rule_id: str,
    kind: str,
    ts: datetime,
    *,
    site_id: str,
    label: str | None = None,
    severity: str = "info",
    confidence: float = 1.0,
    device_id: str = "rules",
    correlation_id: str | None = None,
    track_id: int | None = None,
    zone: str | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    attributes: dict[str, Any] | None = None,
    key: Iterable[object] = (),
) -> Event:
    """Build a RULE-provenance event. `key` disambiguates multiple firings of the
    same rule at the same timestamp (e.g. one per zone)."""
    return Event(
        id=derived_id(rule_id, kind, ts.isoformat(), *key),
        ts_utc=ts,
        site_id=site_id,
        device_id=device_id,
        kind=kind,
        label=label,
        confidence=confidence,
        provenance=Provenance.RULE,
        correlation_id=correlation_id,
        track_id=track_id,
        zone=zone,
        bbox=bbox,
        severity=severity,
        attributes={"rule": rule_id, **(attributes or {})},
    )


class Rule:
    """Base class. Subclasses set `id`, `kinds` (event kinds they consume, empty
    = all), store their config as `cfg`, and override `on_event` and/or `on_tick`.
    `wants` may be overridden for finer filters."""

    id: str = "rule"
    kinds: frozenset[str] = frozenset()

    def __init__(self, ctx: Ctx, cfg: Any) -> None:
        self.ctx = ctx
        self.cfg = cfg

    def on_event(self, event: Event) -> list[Event]:
        return []

    def on_tick(self, now: datetime) -> list[Event]:
        return []

    def wants(self, event: Event) -> bool:
        return not self.kinds or event.kind in self.kinds

    def enabled(self) -> bool:
        return bool(getattr(self.cfg, "enabled", True))

    def emit(self, kind: str, ts: datetime, **kw: Any) -> Event:
        kw.setdefault("site_id", self.ctx.cfg.site_id)
        return derive(self.id, kind, ts, **kw)
