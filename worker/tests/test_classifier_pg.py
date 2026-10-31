"""Classifier SQL against real Postgres (migrations 001+002) with a stub decision server. Skipped without DATABASE_URL."""
import os
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import psycopg
import psycopg.rows
import pytest

from eyeai_worker.classifier import ClassifierConfig, ClassifierService
from eyeai_worker.repo import PgRepository
from stub_decision_server import StubServer

ROOT = Path(__file__).resolve().parents[2]
DSN = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="needs DATABASE_URL")


def _jpeg():
    img = np.full((360, 640, 3), (30, 90, 150), np.uint8)
    return cv2.imencode(".jpg", img)[1].tobytes()


@pytest.fixture()
def db():
    with psycopg.connect(DSN, autocommit=True, row_factory=psycopg.rows.dict_row) as c:
        if not c.execute("SELECT to_regclass('public.sources') AS r").fetchone()["r"]:
            c.execute((ROOT / "db/migrations/001_init.sql").read_text())
        if not c.execute("SELECT 1 AS x FROM information_schema.columns WHERE table_name='events' AND column_name='pipeline'").fetchone():
            c.execute((ROOT / "db/migrations/002_classification.sql").read_text())
        sid = c.execute(
            "INSERT INTO sources (code, name, url, kind, labels) VALUES ('CL-%s','Classifier test','rtsp://x/y','rtsp','{person}') RETURNING id::text AS id"
            % int(time.time() * 1000)).fetchone()["id"]

        def add_event(pipeline="queued", snapshot=True, label="person"):
            eid = c.execute(
                "INSERT INTO events (source_id, label, confidence, severity, bbox, title, pipeline) "
                "VALUES (%s,%s,0.9,'HIGH','{\"x\":0.3,\"y\":0.3,\"w\":0.2,\"h\":0.4}','t',%s) RETURNING id::text AS id",
                (sid, label, pipeline)).fetchone()["id"]
            if snapshot:
                c.execute("INSERT INTO event_snapshots (event_id, idx, jpeg, width, height) VALUES (%s,0,%s,640,360)", (eid, _jpeg()))
            return eid

        yield c, sid, add_event
        c.execute("DELETE FROM sources WHERE id=%s", (sid,))  # cascades to events/snapshots


@pytest.fixture()
def repo():
    r = PgRepository(DSN, max_size=3)
    r.wait_ready()
    yield r
    r.close()


def row(c, eid):
    return c.execute(
        "SELECT pipeline, status, incident_id, cls_label, cls_confidence, cls_probs, cls_model, cls_ms, cls_error, cls_at "
        "FROM events WHERE id=%s", (eid,)).fetchone()


def test_one_iteration_stores_classification_and_leaves_triage_state_alone(db, repo):
    c, sid, add = db
    eid = add()
    with StubServer() as s:
        assert ClassifierService(repo, ClassifierConfig(url=s.url, poll_s=0.01)).run_once() is True
    r = row(c, eid)
    assert r["pipeline"] == "ready" and r["cls_label"] == "person" and r["cls_confidence"] == pytest.approx(0.91)
    assert r["cls_probs"]["false_alarm"] == pytest.approx(0.07) and r["cls_model"] == "d1"
    assert r["cls_ms"] >= 0 and r["cls_at"] is not None
    assert r["status"] == "ready_for_triage" and r["incident_id"] is None  # never dismisses / touches incidents


def test_claim_is_exclusive_and_exclude_list_is_honoured(db, repo):
    c, sid, add = db
    a, b = add(), add()
    j1 = repo.claim_queued_event()
    j2 = repo.claim_queued_event()
    assert {j1.event_id, j2.event_id} == {a, b} and j1.event_id != j2.event_id
    assert j1.bbox["w"] == 0.2 and j1.jpeg[:2] == b"\xff\xd8"
    assert repo.claim_queued_event() is None
    assert row(c, a)["pipeline"] == "classifying"
    repo.store_classification_failure(a, "x" * 500, requeue=True)
    assert repo.claim_queued_event(exclude=[a]) is None          # in backoff
    assert repo.claim_queued_event().event_id == a
    assert len(row(c, a)["cls_error"]) == 300


def test_failure_modes_skip_recover_and_stats(db, repo):
    c, sid, add = db
    nosnap = add(snapshot=False)
    with StubServer(status=500) as s:
        sv = ClassifierService(repo, ClassifierConfig(url=s.url, poll_s=0.01, max_attempts=1))
        assert sv.run_once() is True
    r = row(c, nosnap)
    assert r["pipeline"] == "failed" and "no snapshot" in r["cls_error"]
    assert repo.classifier_stats()["failed_recent"] >= 1

    stuck = add(pipeline="classifying")
    c.execute("UPDATE events SET cls_at = now() - interval '10 minutes' WHERE id=%s", (stuck,))
    fresh = add(pipeline="classifying")
    c.execute("UPDATE events SET cls_at = now() WHERE id=%s", (fresh,))
    assert repo.recover_stuck_classifying(120) >= 1
    assert row(c, stuck)["pipeline"] == "queued" and row(c, fresh)["pipeline"] == "classifying"

    q = add()
    assert repo.skip_all_queued() >= 2
    assert row(c, q)["pipeline"] == "skipped" and row(c, fresh)["pipeline"] == "classifying"


def test_heartbeat_records_classifier_label(db, repo):
    c, sid, add = db
    q = "SELECT classifier FROM worker_heartbeats WHERE worker_id='w-cls-test'"
    repo.heartbeat("w-cls-test", 2, "yolox_s.onnx", "d1 @ http://classifier:11434")
    assert c.execute(q).fetchone()["classifier"] == "d1 @ http://classifier:11434"
    repo.heartbeat("w-cls-test", 2, "yolox_s.onnx", None)
    assert c.execute(q).fetchone()["classifier"] is None
    c.execute("DELETE FROM worker_heartbeats WHERE worker_id='w-cls-test'")


def test_two_services_never_classify_the_same_event_twice(db, repo):
    c, sid, add = db
    ids = [add() for _ in range(6)]
    with StubServer() as s:
        repo2 = PgRepository(DSN, max_size=2)
        repo2.wait_ready()
        try:
            a = ClassifierService(repo, ClassifierConfig(url=s.url, poll_s=0.01))
            b = ClassifierService(repo2, ClassifierConfig(url=s.url, poll_s=0.01))
            ts = [threading.Thread(target=lambda sv=sv: [sv.run_once() for _ in range(6)]) for sv in (a, b)]
            [t.start() for t in ts]
            [t.join() for t in ts]
        finally:
            repo2.close()
        assert len(s.requests) == 6
    assert all(row(c, i)["pipeline"] == "ready" for i in ids)
