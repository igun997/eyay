"""Snapshot burst + indication (docs/ARCHITECTURE.md "Snapshot burst and indication").

After an event is written its track is sampled for `categories.burst_s` seconds; up to `burst_frames` snapshots are
stored in total (each with the track's current box and its offset). Driven from the source's own frame loop: no
thread, and a failing DB write only costs that one sample. When the burst ends (or the stream is lost) the worker
writes `burst_ms`, `burst_done`, `indication_kind`, `indication`, a timeline row and evaluates 'indication' rules.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Mapping, Sequence

from eyeai.detect.tracker import TrackedDetection

from .behavior import Rule, apply_flags, indication_flags
from .models import Category, EventRecord, SourceInfo
from .render import snapshot_jpeg
from .repo import Repository

log = logging.getLogger("eyeai.burst")

MOVE_THRESHOLD = 0.08          # normalised centre displacement
LINGER_MIN_MS = 3000
CROWD_MIN = 3
MAX_PENDING = 20
SnapshotFn = Callable[[], tuple[bytes, int, int]]
BBox = dict[str, float]

_PLURAL = {"person": "people", "bus": "buses"}


def _plural(label: str) -> str:
    return _PLURAL.get(label, label + "s")


def box_of(t: TrackedDetection) -> BBox:
    x1, y1, x2, y2 = t.detection.bbox
    return {"x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1}


def _centre(b: BBox) -> tuple[float, float]:
    return b["x"] + b["w"] / 2, b["y"] + b["h"] / 2


def classify_burst(
    title: str, label: str, boxes: Sequence[BBox | None], burst_ms: int, last_count: int
) -> tuple[str, str]:
    """-> (indication_kind, sentence). `boxes` has one entry per stored snapshot (None = track not visible)."""
    visible = [b for b in boxes if b is not None]
    secs = f"{burst_ms / 1000:.1f} s"
    disp = 0.0
    if len(visible) >= 2:
        (x0, y0), (x1, y1) = _centre(visible[0]), _centre(visible[-1])
        disp = math.hypot(x1 - x0, y1 - y0)
        if disp > MOVE_THRESHOLD:
            return "moving", f"{title} moving across the frame for {secs}"
        if burst_ms >= LINGER_MIN_MS:
            return "lingering", f"{title} lingering in view for {secs}"
    if last_count >= CROWD_MIN:
        return "crowd", f"{last_count} {_plural(label)} in view"
    n = max(1, len(visible))
    return "appeared", f"{title} appeared (seen in {n} snapshot{'s' if n != 1 else ''})"


@dataclass
class _Pending:
    event_id: str
    incident_id: str | None
    label: str
    title: str
    track_id: int
    t0: datetime
    frames: int
    burst_s: float
    taken: int = 1                                   # snapshot 0 is stored with the event
    boxes: list[BBox | None] = field(default_factory=list)
    last_ms: int = 0
    last_count: int = 0
    errors: int = 0

    def due_ms(self) -> float:
        return self.taken * self.burst_s * 1000 / (self.frames - 1)


class BurstManager:
    def __init__(
        self,
        repo: Repository,
        source: SourceInfo,
        categories: Callable[[], Mapping[str, Category]],
        rules: Callable[[], Sequence[Rule]] = lambda: (),
        *,
        max_pending: int = MAX_PENDING,
    ):
        self._repo = repo
        self.source = source
        self._categories = categories
        self._rules = rules
        self._max_pending = max_pending
        self._pending: dict[str, _Pending] = {}

    def set_source(self, source: SourceInfo) -> None:
        self.source = source

    @property
    def pending(self) -> int:
        return len(self._pending)

    # ---- called by the source loop -------------------------------------------------------------------------
    def start(self, rec: EventRecord, tracked: Sequence[TrackedDetection], now: datetime) -> None:
        """Register the burst of a freshly written event (its snapshot 0 is already stored)."""
        cat = self._categories().get(rec.label)
        frames = max(1, int(getattr(cat, "burst_frames", 1) or 1))
        burst_s = float(getattr(cat, "burst_s", 0) or 0)
        track = next((t for t in tracked if t.detection.label == rec.label and t.track_id == rec.track_id), None)
        p = _Pending(rec.event_id, rec.incident_id, rec.label, cat.title if cat else rec.label.capitalize(), rec.track_id,
                     now, frames, burst_s, boxes=[box_of(track) if track else None],
                     last_count=sum(1 for t in tracked if t.detection.label == rec.label))
        if frames <= 1 or burst_s <= 0 or len(self._pending) >= self._max_pending:
            if len(self._pending) >= self._max_pending and frames > 1 and burst_s > 0:
                log.warning("%s: %d bursts pending, event %s keeps a single snapshot", self.source.code, len(self._pending), rec.event_id[:8])
            self._finalize(p, now)
            return
        self._pending[rec.event_id] = p

    def step(self, tracked: Sequence[TrackedDetection], snapshot: SnapshotFn, now: datetime) -> None:
        """Take the next due sample of every pending burst from the current frame."""
        if not self._pending:
            return
        snap: tuple[bytes, int, int] | None = None
        for p in list(self._pending.values()):
            elapsed = (now - p.t0).total_seconds() * 1000
            if elapsed < p.due_ms():
                continue
            if snap is None:
                snap = snapshot()
            track = next((t for t in tracked if t.detection.label == p.label and t.track_id == p.track_id), None)
            box = box_of(track) if track else None
            count = sum(1 for t in tracked if t.detection.label == p.label)
            try:
                with self._repo.transaction() as tx:
                    tx.insert_snapshot(p.event_id, *snap, bbox=box, offset_ms=int(elapsed), idx=p.taken)
            except Exception:  # noqa: BLE001 - lose this sample, keep detecting
                log.exception("%s: storing burst snapshot failed", self.source.code)
                p.errors += 1
            else:
                p.boxes.append(box)
                p.last_ms, p.last_count = int(elapsed), count
            p.taken += 1
            if p.taken >= p.frames or p.errors >= 3:
                del self._pending[p.event_id]
                self._finalize(p, now)

    def flush(self, now: datetime) -> None:
        """Stream lost / worker stopping: finish every pending burst with the snapshots it has."""
        for p in list(self._pending.values()):
            del self._pending[p.event_id]
            self._finalize(p, now)

    # ---- end of a burst ------------------------------------------------------------------------------------
    def _finalize(self, p: _Pending, now: datetime) -> None:
        kind, sentence = classify_burst(p.title, p.label, p.boxes, p.last_ms, p.last_count)
        n = len(p.boxes)
        try:
            with self._repo.transaction() as tx:
                tx.finalize_burst(p.event_id, burst_ms=p.last_ms, kind=kind, indication=sentence)
                if p.incident_id:
                    tx.add_timeline(incident_id=p.incident_id, kind="system", title=f"Indication: {sentence}",
                                    detail=f"{n} snapshot{'s' if n != 1 else ''} over {p.last_ms / 1000:.1f} s",
                                    event_id=p.event_id, at=now)
                apply_flags(tx, p.event_id, indication_flags(self._rules(), p.label, kind, p.last_ms, sentence), now)
        except Exception:  # noqa: BLE001
            log.exception("%s: finalizing burst of %s failed", self.source.code, p.event_id[:8])
            return
        log.info("%s: burst %s done: %s (%d snapshots, %d ms)", self.source.code, p.event_id[:8], sentence, n, p.last_ms)
