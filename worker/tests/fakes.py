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
        eid = f"e{len(self.r.events)}"
        self.r.ev[eid] = dict(incident_id=kw.get("incident_id"), label=kw["label"], severity=kw["severity"],
                              source_id=kw["source_id"], source_name="Dock 2", flags=[], suspicious=False)
        return eid

    def insert_snapshot(self, event_id, jpeg, width, height, *, bbox=None, offset_ms=0, idx=0):
        if self.r.fail_snapshots and idx > 0:
            raise RuntimeError("db down")
        self.r.snapshots.append((event_id, len(jpeg), width, height))
        self.r.snaps.append(dict(event_id=event_id, idx=idx, bbox=bbox, offset_ms=offset_ms))

    def finalize_burst(self, event_id, *, burst_ms, kind, indication):
        self.r.bursts[event_id] = dict(burst_ms=burst_ms, kind=kind, indication=indication, done=True)

    # behaviour rules
    def lock_event(self, event_id):
        from eyeai_worker.behavior import EventState

        e = self.r.ev.get(event_id)
        if e is None:
            return None
        inc = next((i for i in self.r.incidents if i["id"] == e["incident_id"]), None)
        return EventState(id=event_id, incident_id=e["incident_id"], incident_severity=inc["severity"] if inc else None,
                          label=e["label"], severity=e["severity"], source_id=e["source_id"], source_name=e["source_name"],
                          flags=[dict(f) for f in e["flags"]], suspicious=e["suspicious"])

    def set_event_flags(self, event_id, flags, suspicious, severity):
        self.r.ev[event_id].update(flags=flags, suspicious=suspicious, severity=severity)

    def set_incident_severity(self, incident_id, severity):
        next(i for i in self.r.incidents if i["id"] == incident_id)["severity"] = severity

    def link_event_incident(self, event_id, incident_id):
        self.r.ev[event_id]["incident_id"] = incident_id

    def add_timeline(self, **kw):
        self.r.timeline.append(kw)


@dataclass
class FakeRepo:
    incidents: list = field(default_factory=list)
    events: list = field(default_factory=list)
    snapshots: list = field(default_factory=list)
    timeline: list = field(default_factory=list)
    snaps: list = field(default_factory=list)       # per-snapshot rows: event_id, idx, bbox, offset_ms
    ev: dict = field(default_factory=dict)          # event id -> mutable state (flags, severity, incident...)
    bursts: dict = field(default_factory=dict)      # event id -> finalize_burst arguments
    rules: list = field(default_factory=list)
    fail_events: bool = False
    fail_snapshots: bool = False

    def list_behavior_rules(self):
        return list(self.rules)

    @contextlib.contextmanager
    def transaction(self):
        lists = (self.incidents, self.events, self.snapshots, self.timeline, self.snaps)
        marks = [len(x) for x in lists]
        try:
            yield FakeTx(self)
        except Exception:  # emulate rollback
            for lst, m in zip(lists, marks):
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


@dataclass
class FakeClassifierRepo:
    """In-memory stand-in for the classifier half of PgRepository."""

    events: dict = field(default_factory=dict)  # id -> dict(pipeline, bbox, jpeg, label, snapshots, cls_*, flags)
    rules: list = field(default_factory=list)
    rule_loads: int = 0

    def add(self, event_id: str, jpeg: bytes | None, bbox=None, pipeline: str = "queued", label: str = "person",
            snapshots=None):
        self.events[event_id] = dict(pipeline=pipeline, bbox=bbox, jpeg=jpeg, cls_error=None, cls_label=None, label=label,
                                     snapshots=snapshots, flags=[], store_calls=0)

    def list_behavior_rules(self):
        self.rule_loads += 1
        return list(self.rules)

    def claim_queued_event(self, exclude=(), frames=1):
        from eyeai_worker.classifier import ClassifyJob

        for eid, e in self.events.items():
            if e["pipeline"] == "queued" and eid not in exclude:
                e["pipeline"] = "classifying"
                depth = sum(1 for x in self.events.values() if x["pipeline"] == "queued")
                shots = tuple((e["snapshots"] or [(e["jpeg"], e["bbox"])])[:frames]) if e["jpeg"] else ()
                return ClassifyJob(eid, e["bbox"], e["jpeg"], label=e["label"], snapshots=shots, queue_depth=depth)
        return None

    def store_classification(self, event_id, result, flags=(), error=None):
        self.events[event_id].update(pipeline="ready", cls_label=result.label, cls_confidence=result.confidence,
                                     cls_probs=result.probs, cls_model=result.model, cls_ms=result.ms, cls_error=error,
                                     flags=list(flags))
        self.events[event_id]["store_calls"] += 1

    def store_classification_failure(self, event_id, error, *, requeue):
        self.events[event_id].update(pipeline="queued" if requeue else "failed", cls_error=error[:300])

    def skip_all_queued(self):
        n = 0
        for e in self.events.values():
            if e["pipeline"] == "queued":
                e["pipeline"] = "skipped"
                n += 1
        return n

    def recover_stuck_classifying(self, older_than_s):
        n = 0
        for e in self.events.values():
            if e["pipeline"] == "classifying":
                e["pipeline"] = "queued"
                n += 1
        return n

    def classifier_stats(self):
        return {"queued": sum(e["pipeline"] in ("queued", "classifying") for e in self.events.values()),
                "failed_recent": sum(e["pipeline"] == "failed" for e in self.events.values())}
