"""Pre-storage duplicate suppression of presence detections (PDF section 1: no duplicate inference).

Only `object` events are considered. Everything else (line_cross, zone_enter/exit, dwell,
sensor readings, business records) is a countable or stateful event and is never dropped here.

The key is (device_id, zone, label). Within `window_s` of the last admitted detection with
the same key:
- a repeated NATIVE `object` is dropped;
- a LOCAL_MODEL `object` is dropped when a NATIVE one was admitted, because the camera's own
  analytics already covers it;
- a NATIVE `object` arriving after a LOCAL_MODEL one is kept (the camera is authoritative);
- LOCAL_MODEL objects never suppress each other (periodic per-track updates are presence
  heartbeats the zone rules rely on).
"""
from __future__ import annotations

from datetime import datetime

from eyeai.rules.config import DedupCfg
from eyeai.schema import Event, Provenance

_DETECTION = (Provenance.NATIVE, Provenance.LOCAL_MODEL)


class Deduplicator:
    def __init__(self, cfg: DedupCfg) -> None:
        self.cfg = cfg
        self._last: dict[tuple[str, str | None, str | None], tuple[datetime, Provenance]] = {}

    def admit(self, event: Event) -> bool:
        """Return False when the event repeats a recently admitted native detection."""
        if not self.cfg.enabled or event.kind != "object" or event.provenance not in _DETECTION:
            return True
        key = (event.device_id, event.zone, event.label)
        prev = self._last.get(key)
        if prev is not None:
            prev_ts, prev_prov = prev
            close = abs((event.ts_utc - prev_ts).total_seconds()) <= self.cfg.window_s
            if close and prev_prov is Provenance.NATIVE:
                return False
        self._last[key] = (event.ts_utc, event.provenance)
        return True
