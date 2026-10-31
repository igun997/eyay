"""Real Postgres + real YOLOX on a sample video. Skipped unless DATABASE_URL is set."""
import os
import threading
import time
from pathlib import Path

import cv2
import psycopg
import pytest

from eyeai_worker.manager import yolox_factory
from eyeai_worker.repo import PgRepository
from eyeai_worker.runner import SourceRunner

ROOT = Path(__file__).resolve().parents[2]
DSN = os.environ.get("DATABASE_URL")
MODEL = ROOT / "data/models/yolox_s.onnx"
VIDEO = ROOT / "data/samples/car-detection.mp4"

pytestmark = pytest.mark.skipif(
    not (DSN and MODEL.is_file() and VIDEO.is_file()), reason="needs DATABASE_URL, model and sample video"
)


@pytest.fixture()
def db():
    with psycopg.connect(DSN, autocommit=True) as c:
        if not c.execute("SELECT to_regclass('public.sources')").fetchone()[0]:
            c.execute((ROOT / "db/migrations/001_init.sql").read_text())
        sid = c.execute(
            "INSERT INTO sources (code, name, url, kind, labels, min_confidence) "
            "VALUES ('IT-%s', 'Test gate', 'rtsp://placeholder/none', 'rtsp', '{car}', 0.4) RETURNING id::text"
            % int(time.time() * 1000)
        ).fetchone()[0]
        yield c, sid
        c.execute("DELETE FROM incidents WHERE source_id=%s", (sid,))   # cascades to timeline
        c.execute("DELETE FROM sources WHERE id=%s", (sid,))            # cascades to events/snapshots


def test_file_source_writes_events_incident_snapshot_and_state(db):
    c, sid = db
    repo = PgRepository(DSN)
    repo.wait_ready()
    src = next(s for s in repo.list_sources() if s.id == sid)
    runner = SourceRunner(
        src, repo, repo.list_categories, yolox_factory(str(MODEL)),
        target_fps=5, capture_factory=lambda _s: cv2.VideoCapture(str(VIDEO)), pace_fps=25, state_every_s=2,
    )
    runner.start()
    try:
        deadline = time.time() + 60
        n = 0
        while time.time() < deadline and n == 0:
            time.sleep(1)
            n = c.execute("SELECT count(*) FROM events WHERE source_id=%s", (sid,)).fetchone()[0]
        assert n >= 1, "no event written from a video full of cars"
    finally:
        runner.stop()
        repo.close()

    ev = c.execute("SELECT label, confidence, severity, status, bbox, title, incident_id FROM events WHERE source_id=%s ORDER BY detected_at LIMIT 1", (sid,)).fetchone()
    assert ev[0] == "car" and ev[1] >= 0.4 and ev[3] == "ready_for_triage"
    assert ev[4]["w"] > 0 and ev[4]["h"] > 0 and ev[5] == "Car detected at Test gate"
    assert c.execute("SELECT count(*) FROM incidents WHERE source_id=%s", (sid,)).fetchone()[0] >= 1
    assert c.execute("SELECT count(*) FROM incident_timeline t JOIN incidents i ON i.id=t.incident_id WHERE i.source_id=%s AND t.kind='camera'", (sid,)).fetchone()[0] >= 1
    jpeg, w, h = c.execute("SELECT jpeg, width, height FROM event_snapshots s JOIN events e ON e.id=s.event_id WHERE e.source_id=%s LIMIT 1", (sid,)).fetchone()
    assert bytes(jpeg)[:2] == b"\xff\xd8" and w > 0 and h > 0
    st = c.execute("SELECT status, fps, inference_ms, last_seen FROM sources WHERE id=%s", (sid,)).fetchone()
    assert st[0] in ("online", "offline") and st[3] is not None
