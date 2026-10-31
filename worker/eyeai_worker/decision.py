"""Event decision logic (docs/ARCHITECTURE.md "Event flow" steps 1-3). Pure: talks to a Repository only."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Callable, Mapping, Sequence

from eyeai.detect.tracker import TrackedDetection

from .models import Category, EventRecord, SourceInfo
from .repo import Repository

log = logging.getLogger("eyeai.decision")

SnapshotFn = Callable[[], tuple[bytes, int, int]]  # -> jpeg, width, height


class EventDecider:
    """Turns tracked detections into events/incidents for one source.

    An event is written for a label when
      * the label is watched by the source, enabled in `categories`, and confidence >= source.min_confidence,
      * the track was seen in >= `min_hits` frames (drops one-frame flicker),
      * `categories.cooldown_s` has passed since the last event of that label on this source.
    Unreported (new) tracks win over already-reported ones; an already-reported track is reported again
    once the cooldown elapses ("still there"). At most one event per label per call.
    """

    def __init__(
        self,
        repo: Repository,
        source: SourceInfo,
        categories: Callable[[], Mapping[str, Category]],
        *,
        min_hits: int = 2,
        group_window_s: float = 300.0,
    ):
        self._repo = repo
        self.source = source
        self._categories = categories
        self._min_hits = min_hits
        self._group_window = timedelta(seconds=group_window_s)
        self._hits: dict[tuple[str, int], int] = {}
        self._reported: dict[tuple[str, int], datetime] = {}
        self._last_event: dict[str, datetime] = {}

    def set_source(self, source: SourceInfo) -> None:
        self.source = source

    def process(self, tracked: Sequence[TrackedDetection], snapshot: SnapshotFn, now: datetime) -> list[EventRecord]:
        cats = self._categories()
        src = self.source
        watched = set(src.labels)
        by_label: dict[str, list[TrackedDetection]] = {}
        for t in tracked:
            d = t.detection
            key = (d.label, t.track_id)
            self._hits[key] = self._hits.get(key, 0) + 1
            cat = cats.get(d.label)
            if d.label not in watched or cat is None or not cat.enabled:
                continue
            if d.score < src.min_confidence or self._hits[key] < self._min_hits:
                continue
            by_label.setdefault(d.label, []).append(t)
        if len(self._hits) > 2000:  # long-running source: forget stale tracks
            live = {(t.detection.label, t.track_id) for t in tracked}
            self._hits = {k: v for k, v in self._hits.items() if k in live}
            self._reported = {k: v for k, v in self._reported.items() if k in live}

        out: list[EventRecord] = []
        snap: tuple[bytes, int, int] | None = None
        for label, cands in by_label.items():
            cat = cats[label]
            last = self._last_event.get(label)
            if last is not None and (now - last).total_seconds() < cat.cooldown_s:
                continue
            fresh = [t for t in cands if (label, t.track_id) not in self._reported]
            best = max(fresh or cands, key=lambda t: t.detection.score)
            if snap is None:
                snap = snapshot()
            try:
                rec = self._write(cat, best, snap, now)
            except Exception:  # noqa: BLE001 - leave state untouched so the next frame retries
                log.exception("%s: writing event failed", src.code)
                continue
            self._last_event[label] = now
            self._reported[(label, best.track_id)] = now
            out.append(rec)
        return out

    def _write(self, cat: Category, t: TrackedDetection, snap: tuple[bytes, int, int], now: datetime) -> EventRecord:
        src = self.source
        d = t.detection
        x1, y1, x2, y2 = d.bbox
        title = f"{cat.title} detected at {src.name}"
        with self._repo.transaction() as tx:
            incident_id = incident_code = None
            grouped = False
            if cat.opens_incident:
                incident_id = tx.find_open_incident(src.id, d.label, now - self._group_window)
                if incident_id is None:
                    incident_id, incident_code = tx.create_incident(
                        title=title, severity=cat.severity, source_id=src.id, label=d.label, opened_at=now
                    )
                else:
                    grouped = True
            event_id = tx.insert_event(
                source_id=src.id, incident_id=incident_id, label=d.label, confidence=round(d.score, 4),
                severity=cat.severity, track_id=t.track_id,
                bbox={"x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1}, title=title, detected_at=now,
            )
            tx.insert_snapshot(event_id, *snap)
            if incident_id:
                tx.add_timeline(
                    incident_id=incident_id, kind="camera", title=title, event_id=event_id, at=now,
                    detail=f"{src.code} · {d.label.upper()} {d.score:.2f} · track {t.track_id}",
                )
        log.info("%s: event %s %s %.2f track=%s incident=%s%s", src.code, event_id[:8], d.label, d.score,
                 t.track_id, incident_code or incident_id, " (grouped)" if grouped else "")
        return EventRecord(event_id, incident_id, incident_code, d.label, t.track_id, grouped)
