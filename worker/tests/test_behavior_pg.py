"""Burst + behaviour rules against real Postgres (migrations 001-004). Skipped without DATABASE_URL."""
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import numpy as np
import psycopg
import psycopg.rows
import pytest
from eyeai.detect.tracker import TrackedDetection
from eyeai.detect.yolox import Detection

from eyeai_worker.behavior import RuleCache
from eyeai_worker.burst import BurstManager
from eyeai_worker.classifier import ClassifierConfig, ClassifierService
from eyeai_worker.decision import EventDecider
from eyeai_worker.repo import PgRepository
from stub_decision_server import StubServer

ROOT = Path(__file__).resolve().parents[2]
DSN = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="needs DATABASE_URL")
BBOX = {"x": 0.3, "y": 0.3, "w": 0.2, "h": 0.4}
T0 = datetime.now(timezone.utc)


def _jpeg():
    return cv2.imencode(".jpg", np.full((360, 640, 3), (30, 90, 150), np.uint8))[1].tobytes()


@pytest.fixture()
def db():
    with psycopg.connect(DSN, autocommit=True, row_factory=psycopg.rows.dict_row) as c:
        have = lambda sql: c.execute(sql).fetchone()["r"]  # noqa: E731
        col = "SELECT to_regclass('public.events') IS NOT NULL AND EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='events' AND column_name='%s') AS r"
        for name, missing in (
            ("001_init.sql", not have("SELECT to_regclass('public.sources') IS NOT NULL AS r")),
            ("002_classification.sql", not have(col % "pipeline")),
            ("003_snapshot_burst.sql", not have(col % "burst_done")),
            ("004_behavior_rules.sql", not have("SELECT to_regclass('public.behavior_rules') IS NOT NULL AS r")),
        ):
            if missing:
                c.execute((ROOT / "db/migrations" / name).read_text())
        sid = c.execute(
            "INSERT INTO sources (code, name, url, kind, labels) VALUES ('BH-%s','Dock 2','rtsp://x/y','rtsp','{person}') RETURNING id::text AS id"
            % int(time.time() * 1000)).fetchone()["id"]

        def add_event(*, incident_severity=None, severity="LOW", label="person"):
            inc = None
            if incident_severity:
                inc = c.execute("INSERT INTO incidents (title, severity, source_id, label) VALUES ('Person detected at Dock 2',%s,%s,%s) RETURNING id::text AS id",
                                (incident_severity, sid, label)).fetchone()["id"]
            eid = c.execute(
                "INSERT INTO events (source_id, incident_id, label, confidence, severity, bbox, title) "
                "VALUES (%s,%s,%s,0.9,%s,%s,'t') RETURNING id::text AS id",
                (sid, inc, label, severity, psycopg.types.json.Jsonb(BBOX))).fetchone()["id"]
            c.execute("INSERT INTO event_snapshots (event_id, idx, jpeg, width, height, bbox) VALUES (%s,0,%s,640,360,%s)",
                      (eid, _jpeg(), psycopg.types.json.Jsonb(BBOX)))
            return eid, inc

        yield c, sid, add_event
        c.execute("DELETE FROM incidents WHERE source_id=%s", (sid,))
        c.execute("DELETE FROM sources WHERE id=%s", (sid,))


@pytest.fixture()
def repo():
    r = PgRepository(DSN, max_size=4)
    r.wait_ready()
    yield r
    r.close()


def ev(c, eid):
    return c.execute("SELECT * FROM events WHERE id=%s", (eid,)).fetchone()


def run_classifier(repo, **stub_kw):
    with StubServer(**stub_kw) as s:
        ClassifierService(repo, ClassifierConfig(url=s.url, poll_s=0.01)).run_once()
        return s


def test_trigger_opens_an_incident_for_an_event_without_one(db, repo):
    c, sid, add = db
    eid, _ = add(incident_severity=None, severity="LOW")
    s = run_classifier(repo, noul={"weapon": 0.91})
    e = ev(c, eid)
    assert e["pipeline"] == "ready" and e["status"] == "ready_for_triage"          # never changes triage status
    assert e["suspicious"] is True and e["severity"] == "CRITICAL" and e["incident_id"] is not None
    flags = {f["rule"]: f for f in e["cls_flags"]}
    assert set(flags) == {"weapon", "tampering", "concealment", "trespass", "face_covered"}   # enabled classifier rules for a person
    assert flags["weapon"]["prob"] == pytest.approx(0.91) and flags["weapon"]["triggered"] and not flags["tampering"]["triggered"]
    assert sum(1 for r in s.requests if "class" not in r["body"]["questions"]) == 5           # one request per rule
    inc = c.execute("SELECT title, severity, label, status, source_id::text AS sid FROM incidents WHERE id=%s", (e["incident_id"],)).fetchone()
    assert inc == {"title": "Suspicious: Weapon-like object at Dock 2", "severity": "CRITICAL", "label": "person", "status": "open", "sid": sid}
    t = c.execute("SELECT kind, title, detail, event_id::text AS eid FROM incident_timeline WHERE incident_id=%s", (e["incident_id"],)).fetchall()
    assert t == [{"kind": "system", "title": "Behaviour indicator: Weapon-like object", "eid": eid,
                  "detail": "P(yes) 0.91 >= 0.70 - model estimate, needs human review"}]


def test_trigger_escalates_an_existing_incident_and_never_lowers_it(db, repo):
    c, sid, add = db
    eid, inc = add(incident_severity="MEDIUM", severity="MEDIUM")
    run_classifier(repo, noul={"face_covered": 0.9})                                          # MEDIUM rule: no escalation
    assert c.execute("SELECT severity FROM incidents WHERE id=%s", (inc,)).fetchone()["severity"] == "MEDIUM"
    assert ev(c, eid)["suspicious"] is True
    eid2, inc2 = add(incident_severity="HIGH", severity="HIGH")
    run_classifier(repo, noul={"concealment": 0.8, "weapon": 0.95})
    assert c.execute("SELECT severity FROM incidents WHERE id=%s", (inc2,)).fetchone()["severity"] == "CRITICAL"
    assert ev(c, eid2)["severity"] == "CRITICAL"
    eid3, inc3 = add(incident_severity="CRITICAL", severity="CRITICAL")
    run_classifier(repo, noul={"concealment": 0.9})                                           # HIGH rule on CRITICAL incident
    assert c.execute("SELECT severity FROM incidents WHERE id=%s", (inc3,)).fetchone()["severity"] == "CRITICAL"
    assert ev(c, eid3)["severity"] == "CRITICAL"


def test_no_trigger_stores_flags_only(db, repo):
    c, sid, add = db
    eid, inc = add(incident_severity="HIGH", severity="HIGH")
    run_classifier(repo)                                                                       # everything P=0.05
    e = ev(c, eid)
    assert e["suspicious"] is False and e["severity"] == "HIGH" and len(e["cls_flags"]) == 5
    assert c.execute("SELECT count(*) AS n FROM incident_timeline WHERE incident_id=%s", (inc,)).fetchone()["n"] == 0


def test_burst_stores_snapshots_with_boxes_and_the_indication_merges_with_classifier_flags(db, repo):
    c, sid, add = db
    cats = repo.list_categories()
    person = cats["person"]
    assert (person.burst_s, person.burst_frames) == (6, 4)                                     # seeded by migration 003
    src = next(s for s in repo.list_sources() if s.id == sid)
    rules = RuleCache(repo)
    dec = EventDecider(repo, src, repo.list_categories, min_hits=1)
    bm = BurstManager(repo, src, repo.list_categories, rules.get)

    def tr(dx=0.0, track=1):
        return [TrackedDetection(track, Detection("person", 0, 0.92, (0.4 + dx, 0.3, 0.5 + dx, 0.6)))]

    snap = lambda: (_jpeg(), 640, 360)  # noqa: E731
    first = tr()
    (rec,) = dec.process(first, snap, T0)
    bm.start(rec, first, T0)
    # classifier answers BEFORE the burst ends (one ordering) ...
    run_classifier(repo, noul={"weapon": 0.1})
    for sec in (1, 2, 3, 4, 5, 6):
        bm.step(tr(), snap, T0 + timedelta(seconds=sec))                                      # stands still -> lingering
    e = ev(c, rec.event_id)
    assert e["burst_done"] is True and e["burst_ms"] == 6000 and e["indication_kind"] == "lingering"
    assert e["indication"] == "Person lingering in view for 6.0 s"
    flags = {f["rule"]: f for f in e["cls_flags"]}
    assert "weapon" in flags and flags["lingering"]["triggered"] and flags["lingering"]["prob"] is None   # merged, nothing lost
    assert e["suspicious"] is True and e["severity"] == "HIGH"                                # LOW rule never lowers
    snaps = c.execute("SELECT idx, offset_ms, bbox FROM event_snapshots WHERE event_id=%s ORDER BY idx", (rec.event_id,)).fetchall()
    assert [s["idx"] for s in snaps] == [0, 1, 2, 3] and [s["offset_ms"] for s in snaps] == [0, 2000, 4000, 6000]
    assert all(s["bbox"]["w"] == pytest.approx(0.1) for s in snaps)
    titles = [t["title"] for t in c.execute("SELECT title FROM incident_timeline WHERE incident_id=%s ORDER BY at, id", (rec.incident_id,)).fetchall()]
    assert "Indication: Person lingering in view for 6.0 s" in titles and "Behaviour indicator: Lingering in view" in titles


def test_other_ordering_burst_first_then_classifier_keeps_both(db, repo):
    c, sid, add = db
    src = next(s for s in repo.list_sources() if s.id == sid)
    dec = EventDecider(repo, src, repo.list_categories, min_hits=1)
    bm = BurstManager(repo, src, repo.list_categories, RuleCache(repo).get)
    snap = lambda: (_jpeg(), 640, 360)  # noqa: E731
    tr = [TrackedDetection(1, Detection("person", 0, 0.92, (0.4, 0.3, 0.5, 0.6)))]
    (rec,) = dec.process(tr, snap, T0)
    bm.start(rec, tr, T0)
    for sec in (2, 4, 6):
        bm.step(tr, snap, T0 + timedelta(seconds=sec))
    assert ev(c, rec.event_id)["burst_done"]
    run_classifier(repo, noul={"concealment": 0.9})
    e = ev(c, rec.event_id)
    flags = {f["rule"]: f for f in e["cls_flags"]}
    assert flags["lingering"]["triggered"] and flags["concealment"]["triggered"] and len(flags) == 6
    assert e["pipeline"] == "ready" and e["suspicious"]
    assert c.execute("SELECT severity FROM incidents WHERE id=%s", (rec.incident_id,)).fetchone()["severity"] == "HIGH"
    n = c.execute("SELECT count(*) AS n FROM incident_timeline WHERE incident_id=%s AND title LIKE 'Behaviour indicator:%%'", (rec.incident_id,)).fetchone()["n"]
    assert n == 2


def test_list_behavior_rules_returns_only_enabled_rules_in_order(db, repo):
    keys = [r.key for r in repo.list_behavior_rules()]
    assert keys[:6] == ["weapon", "tampering", "concealment", "trespass", "face_covered", "lingering"] and "plate_obscured" not in keys
