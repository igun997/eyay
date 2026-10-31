from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from datetime import datetime

from eyeai.detect.tracker import TrackedDetection
from eyeai.detect.yolox import Detection

from eyeai_worker.models import Category, SourceInfo


class FakeTx:
    def __init__(self, repo: "FakeRepo"):
        self.r = repo

    def find_open_incident(self, source_id, label, since):
        for inc in reversed(self.r.incidents):
            if inc["source_id"] == source_id and inc["label"] == label and inc["status"] == "open" and inc["opened_at"] >= since:
                return inc["id"]
        return None

    def create_incident(self, *, title, severity, source_id, label, opened_at):
        n = len(self.r.incidents) + 1
        self.r.incidents.append(dict(id=f"i{n}", code=f"INC-{n:04d}", title=title, severity=severity,
                                     source_id=source_id, label=label, opened_at=opened_at, status="open"))
        return f"i{n}", f"INC-{n:04d}"

    def insert_event(self, **kw):
        if self.r.fail_events:
            raise RuntimeError("db down")
        self.r.events.append(dict(kw))
        return f"e{len(self.r.events)}"

    def insert_snapshot(self, event_id, jpeg, width, height):
        self.r.snapshots.append((event_id, len(jpeg), width, height))

    def add_timeline(self, **kw):
        self.r.timeline.append(kw)


@dataclass
class FakeRepo:
    incidents: list = field(default_factory=list)
    events: list = field(default_factory=list)
    snapshots: list = field(default_factory=list)
    timeline: list = field(default_factory=list)
    fail_events: bool = False

    @contextlib.contextmanager
    def transaction(self):
        marks = [len(x) for x in (self.incidents, self.events, self.snapshots, self.timeline)]
        try:
            yield FakeTx(self)
        except Exception:  # emulate rollback
            for lst, m in zip((self.incidents, self.events, self.snapshots, self.timeline), marks):
                del lst[m:]
            raise


def source(labels=("person", "car"), min_conf=0.4, name="Dock 2") -> SourceInfo:
    return SourceInfo(id="s1", code="CAM-07", name=name, url="rtsp://x/y", kind="rtsp",
                      labels=tuple(labels), min_confidence=min_conf, enabled=True)


def cats(**over) -> dict[str, Category]:
    base = {
        "person": Category("person", "Person", "HIGH", True, 30, True),
        "car": Category("car", "Car", "MEDIUM", True, 60, True),
        "bus": Category("bus", "Bus", "LOW", False, 60, True),
        "truck": Category("truck", "Truck", "MEDIUM", True, 60, False),
    }
    base.update(over)
    return base


def td(label="person", track=1, score=0.9, bbox=(0.1, 0.2, 0.3, 0.6)) -> TrackedDetection:
    return TrackedDetection(track, Detection(label=label, class_id=0, score=score, bbox=bbox))


def snap():
    return b"jpeg", 640, 360
