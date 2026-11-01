import base64
import json

import cv2
import numpy as np
import pytest

from eyeai_worker.classifier import (
    CROP_MARGIN, STATE, ClassifierConfig, ClassifierError, ClassifierService, DecisionClient, crop_for_classification,
)
from stub_decision_server import GOOD, StubServer
from fakes import FakeClassifierRepo


def jpeg(w=640, h=360, color=(40, 80, 120)) -> bytes:
    img = np.full((h, w, 3), color, np.uint8)
    cv2.rectangle(img, (w // 2, h // 2), (w // 2 + 20, h // 2 + 20), (255, 255, 255), -1)
    return cv2.imencode(".jpg", img)[1].tobytes()


def size(b: bytes):
    h, w = cv2.imdecode(np.frombuffer(b, np.uint8), 1).shape[:2]
    return w, h


def svc(url, repo, **kw):
    cfg = ClassifierConfig(url=url, poll_s=0.01, **kw)
    return ClassifierService(repo, cfg)


# ---- config ---------------------------------------------------------------------------------------------------
def test_config_from_env_defaults_and_disabled():
    c = ClassifierConfig.from_env({})
    assert not c.enabled and c.label is None and c.model == "d1" and c.timeout_s == 60 and c.poll_s == 2 and c.max_attempts == 3
    c = ClassifierConfig.from_env({"CLASSIFIER_URL": "http://classifier:11434/", "CLASSIFIER_MODEL": "clef", "CLASSIFIER_TIMEOUT_S": "5",
                                   "CLASSIFIER_POLL_S": "0.5", "CLASSIFIER_MAX_ATTEMPTS": "2"})
    assert c.enabled and c.url == "http://classifier:11434" and c.label == "clef @ http://classifier:11434"
    assert (c.timeout_s, c.poll_s, c.max_attempts) == (5, 0.5, 2)


# ---- crop math ------------------------------------------------------------------------------------------------
def test_crop_adds_15_percent_margin_of_larger_side():
    # bbox 100x200 px in a 640x360 frame -> margin = 0.15*200 = 30 px each side -> 160x260
    bbox = {"x": 200 / 640, "y": 50 / 360, "w": 100 / 640, "h": 200 / 360}
    w, h = size(crop_for_classification(jpeg(), bbox))
    assert abs(w - 160) <= 2 and abs(h - 260) <= 2
    assert CROP_MARGIN == 0.15


def test_crop_clamps_to_frame_edges():
    bbox = {"x": 0.0, "y": 0.0, "w": 100 / 640, "h": 100 / 360}   # margin would go negative
    w, h = size(crop_for_classification(jpeg(), bbox))
    assert (w, h) == (115, 115)
    bbox = {"x": 600 / 640, "y": 300 / 360, "w": 40 / 640, "h": 60 / 360}  # margin would exceed the frame
    w, h = size(crop_for_classification(jpeg(), bbox))
    assert w <= 640 and h <= 360 and w >= 40 and h >= 60


def test_crop_smaller_than_40px_falls_back_to_full_frame():
    bbox = {"x": 0.5, "y": 0.5, "w": 10 / 640, "h": 10 / 360}   # 10px + 3px margins = 16px
    assert size(crop_for_classification(jpeg(), bbox)) == (640, 360)
    assert size(crop_for_classification(jpeg(), None)) == (640, 360)


def test_crop_rejects_non_image():
    with pytest.raises(ClassifierError):
        crop_for_classification(b"not a jpeg", None)


# ---- request shape + parsing ----------------------------------------------------------------------------------
def test_request_sends_an_explicit_user_agent():
    # Cloudflare in front of hosted endpoints (e.g. the Runpod proxy) answers 403/1010 to urllib's default User-Agent.
    with StubServer() as s:
        DecisionClient(ClassifierConfig(url=s.url, model="d1")).classify(b"\xff\xd8fakejpeg")
    ua = s.requests[0]["headers"]["User-Agent"]
    assert ua.startswith("eyeai-worker/") and "urllib" not in ua.lower()


def test_request_body_matches_the_spec_exactly():
    with StubServer() as s:
        client = DecisionClient(ClassifierConfig(url=s.url, model="d1"))
        r = client.classify(b"\xff\xd8fakejpeg")
    req = s.requests[0]
    assert req["path"] == "/v1/systemone"
    assert req["body"] == {
        "model": "d1",
        "state": "Snapshot from a security camera, cropped around a possible detection.",
        "images": [base64.b64encode(b"\xff\xd8fakejpeg").decode()],
        "questions": {"class": {
            "type": "choice",
            "instructions": "What does this security-camera snapshot show?",
            "criteria": {
                "person": "A person is clearly visible",
                "vehicle": "A car, truck, bus or motorcycle is clearly visible",
                "false_alarm": "No person or vehicle: shadow, lighting, animal, goods or an empty scene",
            },
        }},
    }
    assert STATE == req["body"]["state"]
    assert r.label == "person" and r.confidence == pytest.approx(0.91) and r.probs["false_alarm"] == pytest.approx(0.07)
    assert r.model == "d1" and r.ms >= 0


@pytest.mark.parametrize("bad", [
    {"answers": {}}, {"answers": {"class": {"choice": "banana", "probabilities": {"banana": 1}}}},
    {"answers": {"class": {"choice": "person"}}}, {"nope": 1}, [],
])
def test_malformed_answers_raise(bad):
    with StubServer(body=bad) as s, pytest.raises(ClassifierError):
        DecisionClient(ClassifierConfig(url=s.url)).classify(b"x")


def test_http_500_and_invalid_json_and_unreachable_raise():
    with StubServer(status=500, body={"error": "llama-server terminated"}) as s:
        with pytest.raises(ClassifierError, match="HTTP 500"):
            DecisionClient(ClassifierConfig(url=s.url)).classify(b"x")
    with StubServer(raw=b"<html>") as s, pytest.raises(ClassifierError, match="invalid JSON"):
        DecisionClient(ClassifierConfig(url=s.url)).classify(b"x")
    with pytest.raises(ClassifierError, match="unreachable"):
        DecisionClient(ClassifierConfig(url="http://127.0.0.1:1", timeout_s=2)).classify(b"x")


# ---- service loop ---------------------------------------------------------------------------------------------
def test_success_stores_result_and_only_touches_pipeline_fields():
    repo = FakeClassifierRepo()
    repo.add("e1", jpeg(), {"x": 0.3, "y": 0.3, "w": 0.2, "h": 0.4})
    with StubServer() as s:
        assert svc(s.url, repo).run_once() is True
        sent = base64.b64decode(s.requests[0]["body"]["images"][0])
    e = repo.events["e1"]
    assert e["pipeline"] == "ready" and e["cls_label"] == "person" and e["cls_confidence"] == pytest.approx(0.91)
    assert e["cls_model"] == "d1" and e["cls_probs"]["person"] == pytest.approx(0.91)
    assert size(sent) != (640, 360), "classifier should receive the crop, not the full frame"
    assert "status" not in e and "incident_id" not in e   # the classifier never touches triage state
    assert svc("http://x", repo).run_once() is False  # nothing left


def test_failure_retries_with_backoff_then_fails_for_good():
    repo = FakeClassifierRepo()
    repo.add("e1", jpeg())
    with StubServer(status=500, body={"error": "boom"}) as s:
        sv = svc(s.url, repo, max_attempts=3)
        t = 1000.0
        assert sv.run_once(now=t) is True and repo.events["e1"]["pipeline"] == "queued"        # attempt 1 -> requeued
        assert "HTTP 500" in repo.events["e1"]["cls_error"]
        assert sv.run_once(now=t + 0.001) is False                                              # still in backoff
        assert len(s.requests) == 1
        t += 100
        assert sv.run_once(now=t) is True and repo.events["e1"]["pipeline"] == "queued"        # attempt 2 -> requeued
        t += 100
        assert sv.run_once(now=t) is True and repo.events["e1"]["pipeline"] == "failed"        # attempt 3 -> failed
        assert len(s.requests) == 3
        assert sv.run_once(now=t + 100) is False                                                # failed events are not retried
    assert sv._attempts == {} and sv._retry_at == {}


def test_recovers_after_transient_failure():
    repo = FakeClassifierRepo()
    repo.add("e1", jpeg())
    with StubServer(status=500) as s:
        sv = svc(s.url, repo, max_attempts=3)
        sv.run_once(now=0.0)
        s.status, s.body = 200, GOOD
        assert sv.run_once(now=1000.0) is True
    assert repo.events["e1"]["pipeline"] == "ready" and repo.events["e1"]["cls_error"] is None


def test_event_without_snapshot_fails_without_calling_the_model():
    repo = FakeClassifierRepo()
    repo.add("e1", None)
    with StubServer() as s:
        svc(s.url, repo).run_once()
        assert s.requests == []
    assert repo.events["e1"]["pipeline"] == "failed" and "no snapshot" in repo.events["e1"]["cls_error"]


def test_one_bad_event_does_not_block_the_next():
    repo = FakeClassifierRepo()
    repo.add("e1", b"garbage")
    repo.add("e2", jpeg())
    with StubServer() as s:
        sv = svc(s.url, repo, max_attempts=1)
        sv.run_once(); sv.run_once()
    assert repo.events["e1"]["pipeline"] == "failed" and repo.events["e2"]["pipeline"] == "ready"


def test_no_classifier_configured_marks_queued_as_skipped_and_never_dismisses():
    repo = FakeClassifierRepo()
    repo.add("e1", jpeg()); repo.add("e2", jpeg(), pipeline="ready")
    sv = ClassifierService(repo, ClassifierConfig(url=None, poll_s=0.01))
    assert sv.client is None and not sv.cfg.enabled
    assert repo.skip_all_queued() == 1
    assert repo.events["e1"]["pipeline"] == "skipped" and repo.events["e2"]["pipeline"] == "ready"


def test_run_forever_thread_classifies_and_reports_status():
    import time
    repo = FakeClassifierRepo()
    repo.add("e1", jpeg())
    with StubServer() as s:
        sv = svc(s.url, repo)
        sv.start()
        deadline = time.time() + 5
        while repo.events["e1"]["pipeline"] != "ready" and time.time() < deadline:
            time.sleep(0.02)
        sv.shutdown()
    assert repo.events["e1"]["pipeline"] == "ready"
    st = sv.status()
    assert st["enabled"] and st["model"] == "d1" and st["url"] == s.url and set(st) == {"enabled", "model", "url", "queued", "failed_recent"}


def test_run_forever_disabled_skips_queued_events():
    import time
    repo = FakeClassifierRepo()
    repo.add("e1", jpeg())
    sv = ClassifierService(repo, ClassifierConfig(url=None, poll_s=0.01))
    sv.start()
    deadline = time.time() + 5
    while repo.events["e1"]["pipeline"] != "skipped" and time.time() < deadline:
        time.sleep(0.02)
    sv.shutdown()
    assert repo.events["e1"]["pipeline"] == "skipped"
    assert sv.status()["enabled"] is False and sv.status()["model"] is None
